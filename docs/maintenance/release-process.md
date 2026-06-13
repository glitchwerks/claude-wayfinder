# Release Process

_Maintainer doc — relevant only if you ship releases of `claude-wayfinder` or a fork. Consumers do not need to follow this runbook._

Authoritative release runbook for `claude-wayfinder`. Every command shown uses the `git -C <repo>` pattern (CLAUDE.md § Shell — never `cd <repo> && git ...`).

The **Quick reference card** at the end is the section to keep open during a release. Return to the sections above for rationale and edge-case guidance.

---

## Pre-release checklist

- [ ] All implementing PRs for this release are merged to `main`
- [ ] CI is green on the latest `main` commit (all six jobs: lint, test-py311, test-py312, test-node, skill-smoke-ubuntu, validate-manifest)
- [ ] `CHANGELOG.md` has a draft `## [X.Y.Z] - YYYY-MM-DD` section ready — the heading must use this **exact format**: `## [X.Y.Z] - YYYY-MM-DD`. The `github-release` job's awk extractor strips the leading `v` from the tag and matches on `## [X.Y.Z]`; a non-standard heading produces empty release notes.
- [ ] `pyproject.toml` `version` = target version
- [ ] `.claude-plugin/plugin.json` `version` = target version
- [ ] If `pyproject.toml` dependencies changed: `uv lock` re-run and `uv.lock` included in the release PR
- [ ] Release PR body lists `Closes #N` for every issue being closed (one keyword per issue, plain text, not in commit message scope)

---

## Release classification

| Class | When | Extra steps beyond patch | Cache wipe? |
|---|---|---|---|
| **Patch** (`x.y.Z`) | Bug fixes, doc-only, test-only | — | No |
| **Minor** (`x.Y.0`) | New skills, new commands, backward-compatible additions | Update README skill section if applicable | No |
| **Major** (`X.0.0`) | Breaking changes, schema migrations | Update README; note breaking changes in CHANGELOG | No |
| **Repo move** | `source.repo` in `glitchwerks/claude-plugins` marketplace changes | All of major + cache wipe (step 13) | **Yes** |

The cache wipe applies **only to repo moves**. Pure version bumps — including major bumps — do not need it. See the cache-wipe footgun entry below.

---

## Step-by-step sequence

**1. Open the release PR**

Branch from `main`. Bump `pyproject.toml` `version`, `.claude-plugin/plugin.json` `version`, and `CHANGELOG.md` (move the draft entry to `## [X.Y.Z] - YYYY-MM-DD`). (`src/claude_wayfinder/__init__.py` does **not** need a manual bump — `__version__` self-derives from dist metadata via `importlib.metadata`; see #176.) Include `Closes #N` for every issue. For minor/major: also update `README.md` if skill names, command names, or environment variables changed. If any `pyproject.toml` dependencies changed, re-run `uv lock` and include the updated `uv.lock` in the same PR.

**2. Wait for CI and merge**

CI must be green on all six jobs: `lint`, `test-py311`, `test-py312`, `test-node`, `skill-smoke-ubuntu`, `validate-manifest`. Merge to `main` (squash merge).

**3. Tag the merge commit**

```bash
git -C <repo> pull origin main
git -C <repo> rev-parse HEAD                           # note the merge SHA
git -C <repo> tag -a vX.Y.Z <merge-sha> -m "vX.Y.Z"
```

**4. Push the tag**

```bash
git -C <repo> push origin vX.Y.Z
```

This triggers `release.yml`: `build` → `publish-pypi` + `github-release` (the latter two run in parallel after `build`). Pre-release tags (`-rc`, `-alpha`, `-beta`) publish to TestPyPI instead and are flagged `prerelease: true` on the GH Release. (GH Release auto-creation was automated by PR #189, closing [issue #131](https://github.com/glitchwerks/claude-wayfinder/issues/131).)

**5. Wait for the release workflow**

```bash
gh run list --repo glitchwerks/claude-wayfinder
gh run view <run-id> --repo glitchwerks/claude-wayfinder
```

All jobs must be green. Do not proceed until both `publish-pypi` and `github-release` are confirmed green.

**6. Verify the GitHub Release**

The `github-release` job in `release.yml` creates the GH Release automatically on tag push (PR #189, closing [#131](https://github.com/glitchwerks/claude-wayfinder/issues/131)). Verify:

```bash
gh release view vX.Y.Z --repo glitchwerks/claude-wayfinder
```

Confirm the release exists, the body matches the `## [X.Y.Z]` section of `CHANGELOG.md`, and sdist + wheel are attached as assets. Pre-release tags (`-rc`/`-alpha`/`-beta`) should be flagged `prerelease: true`.

**If the release is missing or the notes look wrong**, inspect the `github-release` job logs — the most likely cause is a mismatch between the tag (`vX.Y.Z`) and the CHANGELOG heading (`## [X.Y.Z]`). The awk extractor strips the leading `v` from the tag; if you used a non-standard tag format the section will be empty.

**7. Dereference the annotated tag**

```bash
git -C <repo> rev-parse 'vX.Y.Z^{commit}'
```

This returns the underlying commit SHA. **Never use bare `git rev-parse vX.Y.Z`** — on annotated tags that returns the tag-object SHA, which the marketplace loader cannot resolve. See Footguns below.

**8. Open the marketplace bump PR**

In `glitchwerks/claude-plugins`, update `.claude-plugin/marketplace.json`:
- `plugins[?name=="claude-wayfinder"].source.sha` → commit SHA from step 7
- `plugins[?name=="claude-wayfinder"].version` → `X.Y.Z`

**9. Merge the marketplace PR**

`glitchwerks/claude-plugins` has no CI (as of 2026-05-20) — squash-merge immediately.

**10. Verify the live pin**

```bash
gh api repos/glitchwerks/claude-plugins/contents/.claude-plugin/marketplace.json \
  --jq '.content' | base64 -d | grep -A 6 '"claude-wayfinder"'
```

Confirm `sha` matches step 7 and `version` matches `X.Y.Z`.

**11. Tell users to re-run `/setup-wayfinder`**

The plugin venv pins an exact version. Without re-running `/setup-wayfinder`, consumers stay silently on the old version. See the footgun entry below.

**12. Sync the buildwithclaude listing (external marketplace)**

`claude-wayfinder` is listed in the community marketplace [davepoon/buildwithclaude](https://github.com/davepoon/buildwithclaude) as a github-source entry (added in davepoon/buildwithclaude#181). That entry mirrors `version` (plus `description` / `keywords`) from this repo's `.claude-plugin/plugin.json`. Refresh it so the public listing stays accurate.

> **Why this is manual — and stays out of `release.yml`.** The target is an *external* repo. A CI-driven cross-repo PR would require a long-lived PAT with write access to a buildwithclaude fork, which we do not want to provision or manage. This is a deliberate manual checklist step. **Do not automate it in `release.yml`.**
>
> **Scope.** For a github-source entry the listed `version` is display/discovery metadata only — installs resolve this repo's live `plugin.json`, so a stale entry never breaks installs. This step keeps the public listing accurate; it is not an install-correctness gate.

a. Sync your `cbeaulieu-gt/buildwithclaude` fork's `main` with upstream:

   ```bash
   git -C <fork> fetch upstream
   git -C <fork> push origin upstream/main:main
   ```

b. Create a branch, then edit `.claude-plugin/marketplace.json` → the `claude-wayfinder` entry → set `version` to `X.Y.Z` (and update `description` / `keywords` if they changed) so it matches this repo's `plugin.json`.

c. Commit the change and push the branch to your fork:

   ```bash
   git -C <fork> add .claude-plugin/marketplace.json
   git -C <fork> commit -m "Update claude-wayfinder to vX.Y.Z"
   git -C <fork> push -u origin sync-claude-wayfinder-vX.Y.Z
   ```

d. Open the PR to upstream:

   ```bash
   gh pr create --repo davepoon/buildwithclaude --base main \
     --head cbeaulieu-gt:sync-claude-wayfinder-vX.Y.Z \
     --title "Update claude-wayfinder to vX.Y.Z"
   ```

**13. [Repo move only] Wipe the per-plugin cache**

Runs only when `source.repo` changed in `marketplace.json`. Do not run for patch/minor/major.

```bash
rm -rf ~/.claude/plugins/cache/glitchwerks/claude-wayfinder/
```

Then open a new Claude Code session and run `/reload-plugins`.

---

## Footguns

### Annotated-tag SHA trap

**Rule:** Use `git rev-parse 'vX.Y.Z^{commit}'` for the marketplace SHA pin. Bare `git rev-parse vX.Y.Z` returns the tag-object SHA on annotated tags, which the marketplace loader cannot resolve.

**Source of truth:** glitchwerks/claude-plugins#20 (proved the tag-object SHA breaks the loader), glitchwerks/claude-plugins#21 (fix).

**Comply:** Always append `^{commit}` (step 7 above).

---

### Marketplace repo bump is required

**Rule:** A release is not installable from `glitchwerks/claude-plugins` until `marketplace.json` is bumped.

**Source of truth:** glitchwerks/claude-plugins#19 (claude-wayfinder v0.4.1 — release was not installable until the marketplace bump landed).

**Comply:** Steps 8–10 are not optional. Verify with step 10 before announcing.

---

### PR body must contain the closing keyword

**Rule:** `Closes #N` must appear in the PR body, not only in commit messages. With squash merge, GitHub synthesizes the merge commit from PR title + body, not source commits. `(#N)` in a Conventional Commits scope is not a closing directive.

**Source of truth:** CLAUDE.md § Pull Requests.

**Comply:** One `Closes #N` line per issue, plain text, in the PR body.

---

### Verify PR open before pushing

**Rule:** Before pushing to an in-flight release branch, confirm the PR is still open. A merged branch accepts pushes silently.

**Source of truth:** CLAUDE.md § Pull Requests; enforced by `hooks/check-pr-open.js`.

**Comply:** `gh pr view <branch>` before each push.

---

### Skill-smoke must pass before proceeding past step 5

**Rule:** A green `build` job does not mean the installed skill works at runtime. `skill-smoke-ubuntu` is the runtime gate: it sets `CLAUDE_WAYFINDER_PIP_SPEC` to the workspace path and runs `tests/integration/test_setup_skill.py` against a fresh venv.

**Source of truth:** `.github/workflows/ci.yml` — the `skill-smoke-ubuntu` job definition.

**Comply:** Confirm `skill-smoke-ubuntu` is green (step 5) before deref (step 7). Do not proceed on build-green alone.

---

### `/setup-wayfinder` re-run is required for every consumer

**Rule:** The plugin venv created by `/setup-wayfinder` pins an exact package version. Publishing a new release to PyPI and bumping the marketplace pin does nothing for existing users until they re-run `/setup-wayfinder`. There is no auto-update.

**Comply:** Always announce the `/setup-wayfinder` re-run requirement in the GH Release notes and in any issue comment closing the release. Step 11 is not optional.

---

### Cache wipe is for repo moves only

**Rule:** Do not wipe `~/.claude/plugins/cache/glitchwerks/claude-wayfinder/` unless `source.repo` in `marketplace.json` actually changed. Wiping on a pure version bump removes the slot for the previous version while users still have it active.

**Source of truth:** Verified during the claude-prospector spike when the plugin was split out of claude-configs; the cached `origin` did not update on a `source.repo` change, confirming the cache slot is keyed to the original repo path and must be wiped only when the path changes.

**Comply:** Check the marketplace PR diff for a `source.repo` change. If unchanged, skip step 13.

---

## Rollback procedure

1. **Yank from PyPI** — use the PyPI web UI (`https://pypi.org/manage/project/claude-wayfinder/releases/`) to yank the version. Yank hides it from unconstrained installs; Delete is irreversible.
2. **Delete the tag** — `git push --delete origin vX.Y.Z` then `git tag -d vX.Y.Z`.
3. **Revert the marketplace pin** — PR on `glitchwerks/claude-plugins` restoring the prior `sha` and `version`. Merge immediately.
4. **Comment on tracking issue** — note the rollback, symptom, and next steps. Do not re-close the issue until a corrected release lands.
5. **Post-mortem** — add a CHANGELOG entry for the reverted version and update the relevant Footguns entry or memory file.

---

## Quick reference card

```
Pre-flight
  [ ] Implementing PRs merged, CI green on main (all 6 jobs)
  [ ] CHANGELOG.md draft section ready (heading must be exactly ## [X.Y.Z] - YYYY-MM-DD)
  [ ] pyproject.toml + plugin.json versions bumped (__init__.py is auto-derived, no manual bump)

 1. Open release PR (version bumps + CHANGELOG entry + Closes #N)
 2. CI green (lint, test-py311, test-py312, test-node,
              skill-smoke-ubuntu, validate-manifest) → merge to main
 3. git -C <repo> pull origin main
    git -C <repo> rev-parse HEAD           # note merge SHA
    git -C <repo> tag -a vX.Y.Z <sha> -m "vX.Y.Z"
 4. git -C <repo> push origin vX.Y.Z      # triggers release.yml: build → publish-pypi + github-release
 5. gh run list --repo glitchwerks/claude-wayfinder
    gh run view <run-id> --repo glitchwerks/claude-wayfinder
    # wait for all jobs green (including publish-pypi and github-release)
 6. gh release view vX.Y.Z --repo glitchwerks/claude-wayfinder
    # auto-created by release.yml on tag push (closed #131)
 7. git -C <repo> rev-parse 'vX.Y.Z^{commit}'   # commit SHA (not tag-obj)
 8. Open PR on glitchwerks/claude-plugins: bump sha + version for claude-wayfinder
 9. Merge marketplace PR
10. gh api repos/glitchwerks/claude-plugins/contents/.claude-plugin/marketplace.json \
      --jq '.content' | base64 -d | grep -A 6 '"claude-wayfinder"'
11. Announce: users must run /setup-wayfinder to pick up the new version
12. Sync buildwithclaude listing:
    a. git -C <fork> fetch upstream && git -C <fork> push origin upstream/main:main
    b. Edit .claude-plugin/marketplace.json → claude-wayfinder entry → version X.Y.Z
    c. git -C <fork> add .claude-plugin/marketplace.json
       git -C <fork> commit -m "Update claude-wayfinder to vX.Y.Z"
       git -C <fork> push -u origin sync-claude-wayfinder-vX.Y.Z
    d. gh pr create --repo davepoon/buildwithclaude --base main \
         --head cbeaulieu-gt:sync-claude-wayfinder-vX.Y.Z \
         --title "Update claude-wayfinder to vX.Y.Z"

Repo move only (source.repo changed):
13. rm -rf ~/.claude/plugins/cache/glitchwerks/claude-wayfinder/
    /reload-plugins in a new Claude Code session
```
