---
name: dispatch
description: >
  Run the claude-wayfinder deterministic 7-decision dispatch matcher
  against bundled demo fixtures and return the decision output for each
  of the 7 routing branches (delegate, self_handle, self_handle_unaided,
  advisory, ambiguous, ask_user, needs_more_detail).
  Invoke this skill to evaluate the matcher or explore its routing logic
  without configuring your own catalog.
triggers:
  command_prefixes:
    - /dispatch
---

# Dispatch Skill

Invoke the bundled demo to see the **claude-wayfinder** deterministic
7-decision matcher in action.

## What this does

Runs `python -m claude_wayfinder demo` against the bundled
`demo-catalog.json` and `demo-prompts.json` fixtures, printing all seven
decision branches with inputs, decisions, confidence scores, and rationale.

## Prerequisites

Python ≥ 3.11 must be on your `$PATH` and `claude-wayfinder` must be
installed (the plugin install step covers this for sideloaded users):

```bash
# Confirm the package is available
python -m claude_wayfinder demo --help
```

If the command is not found, install the package first:

```bash
git clone https://github.com/glitchwerks/claude-wayfinder.git
cd claude-wayfinder
pip install -e ".[dev]"
```

## Running the demo

```bash
python -m claude_wayfinder demo
```

Expected output (7 decision blocks):

```
[1/7] Branch: delegate
  input       : 'implement the authentication module'
  file_paths  : ['src/auth.py']
  decision    : delegate
  confidence  : 0.9000
  agent       : code-writer
  ...

[2/7] Branch: self_handle
  ...

[3/7] Branch: self_handle_unaided
  ...

[4/7] Branch: advisory
  ...

[5/7] Branch: ambiguous
  ...

[6/7] Branch: ask_user
  decision    : ask_user
  rationale   : Reserved — not produced by the v0.1 matcher. ask_user is
                part of the 7-decision contract and reserved for future
                clarification flows.

[7/7] Branch: needs_more_detail
  ...
```

## The 7 decision branches

| Branch              | When it fires                                                   |
|---------------------|-----------------------------------------------------------------|
| `delegate`          | One agent scores ≥ 0.85 with a gap ≥ 0.2 above the next.      |
| `self_handle`       | At least one skill scores ≥ 0.5; no dominant agent.            |
| `self_handle_unaided` | Nothing scores above threshold; proceed without delegation. |
| `advisory`          | Best agent ≥ 0.5 but not conclusive; suggested, not required.  |
| `ambiguous`         | Multiple agents score ≥ 0.5 with gap < 0.2; needs tiebreak.   |
| `ask_user`          | Reserved — not produced by the v0.1 matcher.                   |
| `needs_more_detail` | Feature density < 2; provide more context to route accurately. |

## Bundled demo catalog

The bundled `demo-catalog.json` contains three entries chosen to exercise
all 7 decision branches:

- **`code-writer`** (agent) — keywords: `implement`, `edit`, `review`, `deploy`;
  path globs: `**/*.py`, `**/*.ts`
- **`devops`** (agent) — keywords: `deploy`, `pipeline`, `review`, `infra`;
  path globs: `**/*.yml`, `**/*.yaml`
- **`python`** (skill) — keywords: `python`, `script`; applicable to all agents

## Notes

- `ask_user` is a valid entry in `VALID_DECISIONS` but is **reserved** in
  v0.1 — the matcher never produces it. It is shown in the demo with a
  note explaining its reserved status.
- The v0.1 plugin is an **evaluation surface**, not a daily-driver router.
  To point the matcher at your own agents and skills, see the contributor
  path in the README and the `python -m claude_wayfinder --help` output.
