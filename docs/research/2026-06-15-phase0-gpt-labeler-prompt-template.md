# Phase 0 GPT Labeler Prompt Template

**Used for:** spike #382 — independent (non-Claude) re-labeling of the
168-entry dispatch-log corpus to measure GPT-vs-gold agreement and
establish an independent routing-correctness floor.

**Date:** 2026-06-15
**Issue:** glitchwerks/claude-wayfinder#382

---

> **Taxonomy update notice (#395).** This template records the prompt **as sent in spike #382 (2026-06-15)** and is preserved unedited as a historical record. The `diagnose`/`research` posture boundary was subsequently broadened (#395 / #364 Q2): read-only investigation of how existing/external code behaves — no failure pasted, no prior-art markers — now resolves to `diagnose`, not `research`. Current labeling guidance lives in `docs/research/label-taxonomy.md` §3 and `docs/research/2026-06-12-gold-labeling-rubric.md` §3 Step 1. A future independent-labeler run should use an updated template, not this frozen one.

## Prompt template (sent to `codex exec --sandbox read-only`)

The `{{ENTRIES_JSON}}` placeholder is replaced with the batch's entries,
one JSON object per line. Each entry object has these fields:
- `corpus_id` (int): stable identifier
- `task_description` (str): the dispatch prompt text
- `file_paths` (list[str] | null): file paths present in the prompt context
- `agent_mentions` (list[str] | null): agent names mentioned in the prompt
- `tool_mentions` (list[str] | null): tool names mentioned in the prompt
- `command_prefix` (str | null): command prefix if present

---

```
You are a dispatch-routing labeler. Classify each task into domain and posture
using the rubric below. Output ONLY one JSON object per task, one per line,
nothing else.

ALLOWED VALUES (use only these exact strings):
  domain: code | infra_deploy | data | docs_prose | project_meta
  posture: build | diagnose | assess | critique | verify | plan | research | operate

DOMAIN RULES:
- code: .py/.ts/.go/.js/.rs etc; src/**, tests/**, explicit code references
- infra_deploy: terraform/, bicep/, .github/workflows/, deployment commands
  (az, kubectl, docker, terraform), topology/provider questions
- data: database schemas, migrations, data pipeline files, query languages
- docs_prose: docs/**, *.md, *.rst, README files, prose artifact targets
- project_meta: issue/PR scope, project planning, spec/plan file paths,
  VCS metadata, GitHub/VCS state operations (issue queries, PR queries and
  writes, repo metadata, CI status checks) — even with no file paths

POSTURE RULES (apply in order; first match wins):
1. operate: non-null command_prefix, OR VCS-command shape (git/gh commands)
2. diagnose: machine-emitted failure output in prompt (stacktrace, test-runner
   summary like "FAILED tests/...", compiler diagnostic, panic:) AND cause not
   stated (no causal connective like "after/because/due to/caused by/since/
   introduced by" in the same clause as the failure)
   EXCEPTION: if cause IS stated in same clause as failure → posture = build
3. assess: PR URL, diff hunk, or "PR #N" reference present; OR tool_mentions
   includes get_pull_request*
4. verify: two or more distinct artifact references PLUS relational conformance
   marker ("consistent with", "matches", "conforms to", "drifted from", etc.)
5. critique: challenge-frame markers (adversarial/harsh review) AND either
   code/architecture artifact present → inquisitor path, OR no artifact present
6. plan: no artifact-bearing evidence AND scope-frame markers ("roadmap",
   "phases", "milestones", "scope", "requirements")
7. research: no artifact-bearing evidence AND prior-art markers ("prior art",
   "what exists", "alternatives", "has anyone", "what if we")
8. build: DEFAULT — use when no other posture fires; or when target behavior
   is known and no failure evidence is present

SPECIAL RULES:
- GitHub/VCS state operations → domain: project_meta (even without file paths)
- agent_mentions non-empty with directive intent ("have X do", "use X", "delegate
  to X") → output domain/posture from prompt evidence; ignore for domain/posture
- Harness config files (agents/**/*.md, skills/**/SKILL.md, CLAUDE.md) → still
  label domain/posture from content; do not use "self_handle" (not a valid output)
- If multiple postures are plausible, pick the strongest evidence signal

OUTPUT FORMAT (strict):
One JSON object per input entry, on its own line, NOTHING else:
{"corpus_id": <int>, "domain": "<one of the 5 values>", "posture": "<one of the 8 values>"}

ENTRIES TO LABEL:
{{ENTRIES_JSON}}
```

---

## Extraction logic

From codex output, keep only lines matching `^\{"corpus_id"`.
All other output (process termination lines, "tokens used", etc.) is discarded.
