# Labeling Taxonomy — claude-wayfinder Blind Accuracy Experiment

**Your task:** classify each entry in `label-blind-prompts.jsonl` with a `domain` and a
`posture` value from the taxonomies below.  Emit one JSON object per line to
`labeler-output.jsonl`:

```
{"corpus_id": <int>, "domain": "<value>", "posture": "<value>"}
```

You have NO access to the correct answers — the rubric below is your only guide.

---

## 1. Input fields

Each entry in `label-blind-prompts.jsonl` has:

| Field | What it is |
|-------|------------|
| `corpus_id` | Stable integer ID — **always copy this verbatim into your output** |
| `task_description` | Free-text task description (primary classification signal) |
| `file_paths` | File/directory paths named in the task (may be empty) |
| `agent_mentions` | Explicit agent names referenced (may be empty; used for routing, but classify from task content) |
| `tool_mentions` | Explicit tool names mentioned (may be empty) |
| `command_prefix` | Slash-command or CLI prefix string, or `null` |

Use all fields together; `task_description` carries the most signal but
`file_paths` and `command_prefix` are decisive for certain classes.

---

## 2. Domain — 5-way classification

Classify the **subject matter** of the task.

| Value | When to use | Key signals |
|-------|-------------|-------------|
| `code` | The task produces or modifies source code, tests, or scripts | `.py`, `.ts`, `.go`, `.js`, `.rs`, etc.; paths under `src/**` or `tests/**`; verbs "implement", "write", "fix", "refactor", "add function/method/class" targeting code files |
| `infra_deploy` | The task involves infrastructure, deployment pipelines, or cloud topology | Terraform/Bicep/Pulumi files; `.github/workflows/` paths; `kubectl`, `az`, `docker`, `terraform` commands; "deploy", "provision", "pipeline" framing |
| `data` | The task targets database schemas, migrations, or data pipelines | SQL files, migration scripts, database schema files, query languages |
| `docs_prose` | The task produces or modifies prose documentation | `docs/**`, `*.md`, `*.rst`, README, changelog; verbs "write docs", "update readme", "add changelog entry". **Also:** editing the prose of an existing plan, spec, or design doc (posture = `build`; the deliverable is a modified document) → `docs_prose`. |
| `project_meta` | The task is about project coordination, planning, or VCS state — not producing a code or prose artifact | GitHub issue/PR queries or writes, project planning, CI status checks; `docs/superpowers/specs/` and `docs/superpowers/plans/` paths; **reading** spec/plan docs to scope or phase new work (posture = `plan`; the deliverable is a new plan, not a prose edit) → `project_meta`. **The path alone does not decide domain — the action does.** |

**Special case — `is_any` (domain is genuinely unknowable):** when the prompt is purely
conversational or context-free ("continue", "merge it", "sounds good") with no subject
signal at all, the domain cannot be determined.  Emit `domain: "is_any"` in these rare
cases.  This is NOT the same as ambiguity — ambiguity between two concrete domain values
should be resolved by picking the strongest signal, not by emitting `is_any`.

**Critical clarification — GitHub/VCS operations:** prompts that query or write GitHub
(issue lists, PR status, CI checks, repo metadata) carry `domain: "project_meta"` even
when no file paths are present.  Do NOT use `is_any` for these; the GitHub subject matter
is the domain signal.

### Domain examples

**Ex D1 — `code`**
> "Implement issue #908: modify `src/claude_configs/deploy.py` build_plan Phase 2…"
> `file_paths: ["src/claude_configs/deploy.py"]`

"Implement" + `.py` source file → `code`.

**Ex D2 — `docs_prose`**
> "Update design documentation markdown files to record the harness implementation-language
> decision (shell to Python)…"
> `file_paths: ["docs/design/harness-lang.md"]`

Target is a markdown file under `docs/` → `docs_prose`.  The word "implementation" does
not override the prose artifact target.

**Ex D3 — `project_meta`**
> "Read GitHub pull request nexu-io/open-design#3833 and report what the author is trying to do."
> `file_paths: []`

GitHub PR query, no file target → `project_meta`.

**Ex D4 — `project_meta` (planning)**
> "Plan the drift-aggregation feature: produce an implementation spec/plan."
> `file_paths: []`

Scope/planning ask with no code-artifact target → `project_meta`.

**Ex D5 — `infra_deploy`**
> "Add a GitHub Actions workflow to run CI on every PR."
> `file_paths: [".github/workflows/ci.yml"]`

`.github/workflows/` path → `infra_deploy`.

---

## 3. Posture — 8-way classification

Classify **what the task is asking the agent to do** — the action type, not the subject.

Apply steps in order; the first match wins.

| Posture | When to use | Key signals |
|---------|-------------|-------------|
| `operate` | Execute or query an external system (VCS, CI, cloud) — where the query IS the deliverable | Non-null `command_prefix`; OR VCS-command shape in prompt ("fetch PR #N", "check CI status", "list issues"); OR `tool_mentions` includes `get_pull_request*`, `list_issues`, etc. **Exemption:** a GitHub-issue or PR read that is a MEANS to a subsequent codebase investigation (read issue to understand scope, then explore/grep/read code) does NOT fire `operate` — the dominant intent is investigation → `diagnose` (branch b). `operate` fires only when reading/querying GitHub/VCS state IS the deliverable. |
| `diagnose` | **(a)** Investigate a failure whose cause is unknown, OR **(b)** read-only investigation of how existing/external code or a system *behaves* — no failure pasted, no prior-art markers | **(a)** Machine-emitted failure output pasted in prompt: stacktrace (`Traceback`, `panic:`, `FAILED tests/…::…`, compiler error block); cause **not stated** in the prompt. If "after X" / "because Y" / "due to Z" explains the cause in the same sentence as the failure, flip to `build` instead. **(b)** Task is to comprehend an unfamiliar codebase, external-repo mechanics, or platform behaviour — no failure output pasted, no prior-art / alternatives markers (e.g. "trace how this hook system fires", "how does this library handle X"). |
| `assess` | Review or evaluate an existing artifact | PR URL or `PR #N` present; diff hunk pasted; or `tool_mentions` includes `get_pull_request*`; or the task explicitly asks for a review/evaluation of existing code. |
| `verify` | Check conformance between two distinct artifacts | Two or more named artifacts/sources AND a relational conformance marker ("consistent with", "matches", "conforms to", "drifted from", "should equal"). |
| `plan` | Scope or phase new work — no existing artifact to build on | No file-path artifacts present AND scope-frame markers: "roadmap", "phases", "milestones", "scope", "plan the…", "produce a spec". |
| `research` | Discover prior art or alternatives — the artifact (if any) plays a reference/baseline/constraint role, not the subject role | Prior-art markers: "what exists", "alternatives to", "prior art", "has anyone built", "what are the options". `research` is prior-art/alternatives *discovery* — surveying what already exists. **Subject-vs-reference gate:** `research` MAY apply even when file-path artifacts are present, if the artifact plays a REFERENCE / BASELINE / CONSTRAINT role (seeding an open exploration whose deliverable is discovered options). `research` does NOT apply when the artifact is the SUBJECT being understood or worked — then it is `diagnose` (branch b) or `build`. The distinction is the artifact's ROLE, not its presence. |
| `critique` | Adversarially challenge an idea or architecture | Challenge-frame markers: "what's wrong with", "find flaws in", "argue against", "devil's advocate"; OR harsh-review framing ("give a harsh review", "tear this apart"). |
| `build` | **Default** — construct or modify an artifact when no other posture fires | The task has a clear target artifact (file, module, feature) and the intent is to create/modify it. No failure, no PR-review, no planning scope, no prior-art search, no conformance check. |

**`build` is the unmarked default:** if you are unsure and no other posture fires, choose
`build`.  Do not use ambiguity as a reason to pick a rare posture (verify, plan, research,
critique) over build.

### Posture examples

**Ex P1 — `build`**
> "Add a `--dry-run` flag to the export command."
> `file_paths: ["src/cli/export.py"]`

Target file known, action is "add" → default `build`.

**Ex P2 — `diagnose`**
> "Getting this on every run: `Traceback (most recent call last): File 'src/ingest.py',
> line 42, in run — KeyError: 'session_id'`. Never saw it before."

Machine stacktrace, cause not stated → `diagnose`.

**Ex P3 — `build` (E6 flip — cause stated)**
> "Got `FAILED tests/test_router.py::test_dispatch — AssertionError`. Broke after we
> renamed `route()` to `dispatch()` last PR. Fix the tests."

E1/E2 fires (FAILED test output) BUT cause is stated ("after we renamed") → flip to
`build`.

**Ex P4 — `operate`**
> "Fetch the current status of PR #11723 in warpdotdev/warp."
> `command_prefix: null`, `tool_mentions: ["get_pull_request"]`

GitHub read query / tool mention of `get_pull_request` → `operate`.

**Ex P5 — `plan`**
> "Plan the drift-aggregation feature: scope and phase a new CLI command across milestones."
> `file_paths: []`

No artifact, scope-frame markers ("plan", "phase") → `plan`.

**Ex P6 — `assess`**
> "Review PR #42: does the new caching layer introduce any correctness issues?"

PR reference + review intent → `assess`.

**Ex P7 — `research`**
> "What Python libraries already exist for deterministic JSON diff? List alternatives."
> `file_paths: []`

Prior-art markers, no artifact → `research`.

**Ex P8 — `verify`**
> "Confirm that `src/api.py` is consistent with the contract in `docs/api-spec.md`."

Two artifacts + "consistent with" → `verify`.

---

## 4. Hard cases and disambiguation notes

### Domain hard cases

- **"Implement" + `docs/**` file path:** if the target file is under `docs/` or is a
  `.md` file, classify `docs_prose` even if the verb is "implement".  The target artifact
  is prose, not code.

- **Plan/spec/design doc paths (`docs/superpowers/specs/`, `docs/superpowers/plans/`, or
  any plan/spec/design `.md` under `docs/`) — classify by ACTION, not path:**
  - EDITING the prose of an existing plan/spec/design doc (posture = `build`; the deliverable
    is the modified document itself) → `docs_prose`.
  - READING a plan/spec doc to scope or phase new work (posture = `plan`; the deliverable
    is a new plan or implementation roadmap) → `project_meta`.
  - Worked example: corpus **34712** edits `docs/superpowers/plans/2026-06-04-…md` to add a
    new slice — the deliverable is the modified plan document → `docs_prose` (posture `build`).

- **GitHub workflow files (`.github/workflows/*.yml`):** these are `infra_deploy` even
  though they are YAML (not code in the traditional sense).

### Posture hard cases

- **"Plan" verb but a code file target is present:** if `file_paths` includes a code
  file, the task is more likely `build` (the "plan" is just framing; the output is a
  code artifact).  `plan` posture requires *no artifact target*.

- **Failure vocabulary without machine output:** words like "failing", "broken", "errors
  out" without an actual pasted stacktrace/test-runner block do NOT fire `diagnose`.
  Classify based on remaining signals (usually `build` if the cause is known or implied).

- **PR present + harsh-review framing:** "give PR #N a harsh review" → `critique`
  (adversarial), not `assess` (neutral review).  "Review PR #N for correctness" → `assess`.

- **Read-only investigation of existing/external code with no failure:** understanding how an unfamiliar or external codebase *behaves* (e.g. "trace how repo X's hook system fires", "how does this library handle Y") is `diagnose` (branch b), even though no stacktrace is pasted — provided there are no prior-art/alternatives markers. If the task instead asks "what alternatives exist" / "what's already out there", it is `research`.

- **Incidental GitHub/VCS read before codebase investigation (`operate` exemption):** when a
  GitHub-issue or PR read is a MEANS to a subsequent codebase investigation — read an issue
  to understand scope, THEN explore/grep/read code to assess feasibility or behaviour — the
  dominant intent is investigation: posture = `diagnose` (branch b), NOT `operate`. `operate`
  fires only when the GitHub/VCS query is itself the deliverable. Worked example: corpus
  **35266 / 35297** read `nexu-io/open-design#3808`, then explore the codebase to scope
  feasibility → `diagnose`, not `operate`.

- **`research` with artifact present — subject-vs-reference gate:** `research` MAY apply even
  when file-path artifacts are present, if the artifact plays a REFERENCE / BASELINE /
  CONSTRAINT role (seeding an open exploration). `research` does NOT apply when the artifact
  is the SUBJECT being understood or worked — that is `diagnose` (branch b) or `build`.
  Worked resolution: corpus **35414** reads `src/baton_harness/` to design a PAT-permission
  validation — the code is the SUBJECT being investigated → `diagnose`, gold_agent
  `investigator` (not `research`/`researcher`).

---

## 5. Allowed value lists

**domain** (exactly one of):
```
code | infra_deploy | data | docs_prose | project_meta | is_any
```

**posture** (exactly one of):
```
build | diagnose | assess | critique | verify | plan | research | operate
```

---

## 6. Output format

One JSON object per line, written to `labeler-output.jsonl`:

```jsonl
{"corpus_id": 31093, "domain": "code", "posture": "build"}
{"corpus_id": 31094, "domain": "project_meta", "posture": "operate"}
```

Rules:
- Output **exactly one line per input entry** (109 total).
- Copy `corpus_id` verbatim — do not renumber.
- Use only the allowed values above — no free text, no null, no extra fields.
- Order does not matter; the scorer joins on `corpus_id`.

---

## 7. Disputed entries

If a prompt is genuinely ambiguous between two domain or posture values after applying
the rules above, pick the stronger signal.  Do not emit extra fields — just make a call.
The scorer measures accuracy; your honest best guess is the right input.

**Resolved dispute — corpus 35414 (2026-06-19):** previously disputed between `research`
(researcher) and `diagnose` (investigator) pending the subject-vs-reference research-gate
refinement (#407). Resolved under R3: `src/baton_harness/` is the SUBJECT being
investigated to design PAT-permission validation, not a reference/baseline seeding open
alternatives discovery → `posture: "diagnose"`, `gold_agent: "investigator"`. No longer
disputed. The `disputed` flag flip in the gold artifact is handled by the labeler; this
entry records the prose ruling.
