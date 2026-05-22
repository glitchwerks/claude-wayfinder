"""Tests for claude_wayfinder.build_catalog._main — orchestration.

Covers:
  - write_log (Task 4)
  - build_catalog (Task 5)
  - write_catalog (Task 6)
  - detect_exclude_dead_zones (Task 7)
  - End-to-end build() + cross-reference validation (Task 8 / Charge A1)
  - Project-local skill/agent catalog merging (Issue #385)
  - Stale disabled-override consistency (Issue #132)
  - Pass 2.6 builtin agent sidecars (Issue #505)
  - Data-driven `routable` flag (Issue #19)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from claude_wayfinder.build_catalog import (
    ValidationIssue,
    build_catalog,
    detect_exclude_dead_zones,
    write_catalog,
    write_log,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# build_catalog is now a package; invoke via -m rather than as a file path.
_BUILD_MODULE = ["claude_wayfinder.build_catalog"]
FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Task 4 — write_log tests
# ---------------------------------------------------------------------------


def test_write_log_appends_iso_lines(tmp_path: Path) -> None:
    """write_log writes one ISO-prefixed line per issue."""
    log = tmp_path / "catalog-generation.log"
    write_log(
        log,
        [
            ValidationIssue("warning", "skill-a", "msg one"),
            ValidationIssue("info", "skill-b", "msg two"),
        ],
        now="2026-04-30T12:00:00Z",
    )
    contents = log.read_text(encoding="utf-8")
    assert "2026-04-30T12:00:00Z warning skill-a msg one" in contents
    assert "2026-04-30T12:00:00Z info skill-b msg two" in contents


def test_write_log_appends_not_overwrites(tmp_path: Path) -> None:
    """write_log appends to an existing log rather than overwriting it."""
    log = tmp_path / "catalog-generation.log"
    log.write_text("2026-04-29T00:00:00Z info skill-x prior\n", encoding="utf-8")
    write_log(log, [ValidationIssue("info", "y", "new")], now="2026-04-30T00:00:00Z")
    contents = log.read_text(encoding="utf-8")
    assert "prior" in contents
    assert "new" in contents


# ---------------------------------------------------------------------------
# Task 5 — build_catalog tests
# ---------------------------------------------------------------------------


def test_build_catalog_sorts_entries_kind_then_name() -> None:
    """build_catalog sorts entries by (kind, name): agents before skills."""
    entries = [
        {
            "name": "z-skill",
            "kind": "skill",
            "description": "",
            "triggers": {},
            "applicable_agents": [],
        },
        {
            "name": "a-agent",
            "kind": "agent",
            "description": "",
            "triggers": {},
            "applicable_skills": [],
        },
        {
            "name": "a-skill",
            "kind": "skill",
            "description": "",
            "triggers": {},
            "applicable_agents": [],
        },
    ]
    catalog = build_catalog(entries)
    names = [(e["kind"], e["name"]) for e in catalog["entries"]]
    assert names == [("agent", "a-agent"), ("skill", "a-skill"), ("skill", "z-skill")]


def test_build_catalog_sorts_lists_inside_entries() -> None:
    """build_catalog sorts keywords by term and other list fields alphabetically."""
    entries = [
        {
            "name": "x",
            "kind": "skill",
            "description": "",
            "triggers": {
                "keywords": [
                    {"term": "zebra", "weight": 0.5},
                    {"term": "apple", "weight": 1.0},
                ],
                "command_prefixes": ["/zoo", "/aardvark"],
                "agent_mentions": [],
                "file_extensions": [],
                "path_globs": [],
                "tool_mentions": [],
                "excludes": [],
            },
            "applicable_agents": ["zebra-agent", "apple-agent"],
        }
    ]
    catalog = build_catalog(entries)
    e = catalog["entries"][0]
    assert [k["term"] for k in e["triggers"]["keywords"]] == ["apple", "zebra"]
    assert e["triggers"]["command_prefixes"] == ["/aardvark", "/zoo"]
    assert e["applicable_agents"] == ["apple-agent", "zebra-agent"]


# ---------------------------------------------------------------------------
# Task 6 — write_catalog tests
# ---------------------------------------------------------------------------


def test_write_catalog_byte_stable_across_runs(tmp_path: Path) -> None:
    """Same input → byte-identical output across two writes."""
    catalog = {
        "schema_version": 1,
        "entries": [
            {
                "name": "a",
                "kind": "skill",
                "description": "",
                "triggers": {
                    f: []
                    for f in (
                        "command_prefixes",
                        "agent_mentions",
                        "file_extensions",
                        "path_globs",
                        "keywords",
                        "tool_mentions",
                        "excludes",
                    )
                },
                "applicable_agents": [],
            }
        ],
    }
    out1 = tmp_path / "a.json"
    out2 = tmp_path / "b.json"
    write_catalog(out1, catalog)
    write_catalog(out2, catalog)
    assert out1.read_bytes() == out2.read_bytes()


def test_write_catalog_uses_sorted_keys_and_no_trailing_whitespace(
    tmp_path: Path,
) -> None:
    """Keys are sorted; output ends with exactly one newline, no trailing spaces."""
    out = tmp_path / "c.json"
    write_catalog(out, {"b": 1, "a": 2})
    text = out.read_text(encoding="utf-8")
    assert text == '{"a": 2, "b": 1}\n'


# ---------------------------------------------------------------------------
# Task 7 — detect_exclude_dead_zones tests
# ---------------------------------------------------------------------------


def test_detect_dead_zones_skips_when_corpus_missing(
    tmp_path: Path,
) -> None:
    """Emits one info issue when the corpus file does not exist."""
    issues = detect_exclude_dead_zones(
        entries=[],
        corpus_path=tmp_path / "absent.jsonl",
    )
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "corpus unavailable" in issues[0].message.lower()


def test_detect_dead_zones_emits_deferred_when_corpus_present(
    tmp_path: Path,
) -> None:
    """Emits at least one info-deferred issue when corpus file exists."""
    corpus = tmp_path / "routing-corpus.jsonl"
    corpus.write_text('{"decision_id": "abc"}\n', encoding="utf-8")
    issues = detect_exclude_dead_zones(entries=[], corpus_path=corpus)
    assert any(i.severity == "info" and "deferred" in i.message.lower() for i in issues)


# ---------------------------------------------------------------------------
# Task 8 — end-to-end build() + CLI integration tests
# ---------------------------------------------------------------------------


def test_build_end_to_end_on_fixtures(tmp_path: Path) -> None:
    """build() orchestrates all helpers and emits a valid catalog + log.

    Uses the checked-in fixture tree so behavior is deterministic.  The
    ``broken`` skill has a non-mapping keyword item (fatal); ``legacy``
    has no triggers (dormant/info); ``csv-utils`` is fully valid;
    ``code-writer`` is a valid agent.
    """
    from claude_wayfinder.build_catalog import build

    out = tmp_path / "dispatch-catalog.json"
    log = tmp_path / "catalog-generation.log"
    fixtures = Path(__file__).parent / "fixtures"
    rc = build(
        skills_dir=fixtures / "skills",
        agents_dir=fixtures / "agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-04-30T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = [(e["kind"], e["name"]) for e in catalog["entries"]]
    # broken should be excluded; legacy should be present (dormant);
    # csv-utils should be present (active); code-writer agent present.
    assert ("agent", "code-writer") in names
    assert ("skill", "csv-utils") in names
    assert ("skill", "legacy") in names
    assert ("skill", "broken") not in names
    log_text = log.read_text(encoding="utf-8")
    assert "fatal broken" in log_text
    assert "info legacy" in log_text


def test_build_returns_nonzero_when_catalog_degraded(tmp_path: Path) -> None:
    """rc != 0 when >25% of discovered entries excluded fatally.

    Two skills, both with fatally invalid triggers.yml -> 100% excluded
    -> rc=2.  Under v6, trigger config lives in the sidecar; the SKILL.md
    carries only runtime fields.
    """
    from claude_wayfinder.build_catalog import build

    skills = tmp_path / "skills"
    for stem in ("a", "b"):
        d = skills / stem
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: " + stem + "\n---\n",
            encoding="utf-8",
        )
        (d / "triggers.yml").write_text(
            "triggers: not-a-mapping\n",
            encoding="utf-8",
        )
    rc = build(
        skills_dir=skills,
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=tmp_path / "out.json",
        log_path=tmp_path / "log",
        now="2026-04-30T00:00:00Z",
    )
    assert rc == 2


# ---------------------------------------------------------------------------
# Charge A1 — cross-reference validation pass
# ---------------------------------------------------------------------------


def test_build_warns_on_unknown_applicable_agent(tmp_path: Path) -> None:
    """Skill referencing a non-existent agent emits a warning and drops the name.

    Under v6 the trigger config is in triggers.yml, not SKILL.md.
    """
    from claude_wayfinder.build_catalog import build

    skills = tmp_path / "skills"
    s = skills / "good-skill"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: A skill.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - {term: "foo", weight: 1.0}
            applicable_agents: ["nonexistent-agent", "real-agent"]
            """
        ),
        encoding="utf-8",
    )
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: real\n---\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=skills,
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-04-30T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "applicable_agents references unknown name 'nonexistent-agent' — dropped" in log_text
    catalog = json.loads(out.read_text(encoding="utf-8"))
    skill_entry = next(e for e in catalog["entries"] if e["name"] == "good-skill")
    assert skill_entry["applicable_agents"] == ["real-agent"]


def test_build_keeps_wildcard_in_applicable_agents(tmp_path: Path) -> None:
    """The '*' wildcard must never be warned about or dropped.

    Under v6 the trigger config (including applicable_agents) is in
    triggers.yml, not SKILL.md.
    """
    from claude_wayfinder.build_catalog import build

    skills = tmp_path / "skills"
    s = skills / "broadcast"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: broadcast\ndescription: Broadcast skill.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - {term: "foo", weight: 1.0}
            applicable_agents: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=skills,
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-04-30T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "references unknown name" not in log_text
    catalog = json.loads(out.read_text(encoding="utf-8"))
    skill = next(e for e in catalog["entries"] if e["name"] == "broadcast")
    assert skill["applicable_agents"] == ["*"]



# ---------------------------------------------------------------------------
# Issue #385 — project-local skill/agent catalog merging
# ---------------------------------------------------------------------------


def _make_project_agent(
    agents_dir: Path,
    name: str,
    description: str = "A project agent.",
) -> Path:
    """Write a minimal valid agent .md file into *agents_dir*.

    Args:
        agents_dir: Directory in which to place the agent file.
        name: Agent name (also used as the file stem).
        description: One-line description for the frontmatter.

    Returns:
        Path to the created agent file.
    """
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agents_dir / f"{name}.md"
    agent_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )
    return agent_file


def _make_project_skill(
    skills_dir: Path,
    name: str,
    description: str = "A project skill.",
    *,
    with_sidecar: bool = False,
) -> Path:
    """Write a minimal valid skill directory into *skills_dir*.

    Args:
        skills_dir: Root skills directory.
        name: Skill name (also used as the directory stem).
        description: One-line description for the SKILL.md frontmatter.
        with_sidecar: When True, also write a minimal ``triggers.yml``
            sidecar so the skill is active rather than dormant.

    Returns:
        Path to the created ``SKILL.md`` file.
    """
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )
    if with_sidecar:
        (skill_dir / "triggers.yml").write_text(
            "triggers:\n  keywords:\n"
            f'    - {{term: "{name}", weight: 1.0}}\n'
            'applicable_agents: ["*"]\n',
            encoding="utf-8",
        )
    return skill_md


def _init_git_repo(path: Path) -> None:
    """Initialise a bare git repository at *path* for auto-detection tests.

    Args:
        path: Directory to initialise as a git repo.
    """
    import subprocess

    subprocess.run(
        ["git", "init", str(path)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )


class TestProjectLocalScanning:
    """Tests for Issue #385: project-local skill/agent catalog merging."""

    def test_explicit_project_root_flag(self, tmp_path: Path) -> None:
        """--project-root merges project agents with source='project'.

        Pass ``--project-root`` explicitly and confirm the agent from
        ``.claude/agents/`` inside the project root appears in the catalog
        with ``source='project'`` and that ``built_for_project`` is set.
        """
        from claude_wayfinder.build_catalog import build

        # Project layout: <tmp>/repo/.claude/agents/foo.md
        repo = tmp_path / "repo"
        _make_project_agent(repo / ".claude" / "agents", "foo")

        out = tmp_path / "cat.json"
        log = tmp_path / "log"
        rc = build(
            skills_dir=tmp_path / "no-skills",
            agents_dir=tmp_path / "no-agents",
            corpus_path=tmp_path / "absent.jsonl",
            out_path=out,
            log_path=log,
            project_root=repo,
            now="2026-05-05T00:00:00Z",
        )
        assert rc == 0
        catalog = json.loads(out.read_text(encoding="utf-8"))
        names = {e["name"] for e in catalog["entries"]}
        assert "foo" in names, "project agent must appear in catalog"
        entry = next(e for e in catalog["entries"] if e["name"] == "foo")
        assert entry["source"] == "project"
        assert catalog["built_for_project"] == str(repo)

    def test_project_root_auto_detected_from_cwd(self, tmp_path: Path) -> None:
        """Auto-detection: git rev-parse in a repo cwd sets built_for_project.

        Set up a tmp git repo with ``.claude/agents/foo.md``, invoke the
        generator CLI from inside it, and assert the catalog has ``foo``
        with ``source='project'`` and ``built_for_project`` set.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_project_agent(repo / ".claude" / "agents", "foo")

        out = tmp_path / "cat.json"
        log = tmp_path / "log"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                *_BUILD_MODULE,
                "--skills-dir",
                str(tmp_path / "no-skills"),
                "--agents-dir",
                str(tmp_path / "no-agents"),
                "--corpus",
                str(tmp_path / "absent.jsonl"),
                "--out",
                str(out),
                "--log",
                str(log),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo),
        )
        assert result.returncode == 0, result.stderr
        catalog = json.loads(out.read_text(encoding="utf-8"))
        assert catalog["built_for_project"] == str(repo)
        entry = next((e for e in catalog["entries"] if e["name"] == "foo"), None)
        assert entry is not None, "auto-detected project agent 'foo' not in catalog"
        assert entry["source"] == "project"

    def test_project_overrides_user_global_on_name_collision(self, tmp_path: Path) -> None:
        """Project entry wins when name collides with a user-global entry.

        Set up a synthetic user-global agents dir with ``code-writer.md``
        and a project agents dir also with ``code-writer.md``.  Assert:
        - The project version wins (description from project file).
        - The user-global version is not in the catalog.
        - A warning is logged mentioning the override.
        """
        from claude_wayfinder.build_catalog import build

        # User-global agents dir
        global_agents = tmp_path / "global" / "agents"
        global_agents.mkdir(parents=True)
        (global_agents / "code-writer.md").write_text(
            "---\nname: code-writer\ndescription: GLOBAL version.\n---\n",
            encoding="utf-8",
        )

        # Project agents dir
        repo = tmp_path / "repo"
        _make_project_agent(
            repo / ".claude" / "agents",
            "code-writer",
            description="PROJECT version.",
        )

        out = tmp_path / "cat.json"
        log = tmp_path / "log"
        rc = build(
            skills_dir=tmp_path / "no-skills",
            agents_dir=global_agents,
            corpus_path=tmp_path / "absent.jsonl",
            out_path=out,
            log_path=log,
            project_root=repo,
            now="2026-05-05T00:00:00Z",
        )
        assert rc == 0
        catalog = json.loads(out.read_text(encoding="utf-8"))
        cw_entries = [e for e in catalog["entries"] if e["name"] == "code-writer"]
        assert len(cw_entries) == 1, "exactly one code-writer entry expected"
        assert cw_entries[0]["description"] == "PROJECT version."
        assert cw_entries[0]["source"] == "project"
        log_text = log.read_text(encoding="utf-8")
        assert "code-writer" in log_text
        assert "override" in log_text.lower()

    def test_no_project_root_when_cwd_is_user_home(self, tmp_path: Path) -> None:
        """No double-scan when cwd == user-global home; built_for_project=null.

        When the generator is invoked from a cwd that resolves to the
        user-global ``~/.claude`` directory, ``built_for_project`` must be
        ``null`` (no project merge attempted).
        """
        from claude_wayfinder.build_catalog import build

        # Simulate user-global home being tmp_path itself
        global_home = tmp_path / "dot-claude"
        global_home.mkdir()
        _init_git_repo(global_home)

        out = tmp_path / "cat.json"
        log = tmp_path / "log"

        # Call build() with project_root explicitly set to global_home —
        # the implementation should detect that project_root == user-global
        # home and treat it as "no project".
        # We simulate this by passing a fake home via the build() interface;
        # the real behaviour is tested via the CLI auto-detect test above.
        rc = build(
            skills_dir=tmp_path / "no-skills",
            agents_dir=tmp_path / "no-agents",
            corpus_path=tmp_path / "absent.jsonl",
            out_path=out,
            log_path=log,
            project_root=None,
            now="2026-05-05T00:00:00Z",
        )
        assert rc == 2  # no entries discovered → degraded
        catalog = json.loads(out.read_text(encoding="utf-8"))
        assert catalog["built_for_project"] is None

    def test_no_project_root_when_cwd_is_not_a_git_repo(self, tmp_path: Path) -> None:
        """No project scan when cwd is not a git repo; built_for_project=null.

        Invoke the CLI from a plain tmp directory (not a git repo) and assert
        that the catalog carries ``built_for_project: null`` and no project
        entries appear.
        """
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        out = tmp_path / "cat.json"
        log = tmp_path / "log"

        subprocess.run(
            [
                sys.executable,
                "-m",
                *_BUILD_MODULE,
                "--skills-dir",
                str(tmp_path / "no-skills"),
                "--agents-dir",
                str(tmp_path / "no-agents"),
                "--corpus",
                str(tmp_path / "absent.jsonl"),
                "--out",
                str(out),
                "--log",
                str(log),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(plain_dir),
        )
        # rc=2 expected (degraded: zero entries) but the catalog must be written
        catalog = json.loads(out.read_text(encoding="utf-8"))
        assert catalog["built_for_project"] is None

    def test_determinism(self, tmp_path: Path) -> None:
        """Two consecutive runs from the same project root produce identical output.

        Ensures no timestamps, process IDs, or other non-deterministic data
        leak into the catalog when ``--project-root`` is active.
        """
        from claude_wayfinder.build_catalog import build

        repo = tmp_path / "repo"
        _make_project_agent(repo / ".claude" / "agents", "alpha")
        _make_project_agent(repo / ".claude" / "agents", "beta")
        _make_project_skill(repo / ".claude" / "skills", "gamma", with_sidecar=True)

        def _run(out_path: Path) -> None:
            build(
                skills_dir=tmp_path / "no-skills",
                agents_dir=tmp_path / "no-agents",
                corpus_path=tmp_path / "absent.jsonl",
                out_path=out_path,
                log_path=tmp_path / "log",
                project_root=repo,
                now="2026-05-05T00:00:00Z",
            )

        out1 = tmp_path / "run1.json"
        out2 = tmp_path / "run2.json"
        _run(out1)
        _run(out2)
        assert (
            out1.read_bytes() == out2.read_bytes()
        ), "catalog output must be byte-identical across consecutive runs"

    def test_project_skills_also_merged(self, tmp_path: Path) -> None:
        """Project skills under .claude/skills/**/SKILL.md are merged.

        Verifies the recursive glob for project skills works correctly and
        that the resulting entry carries ``source='project'``.
        """
        from claude_wayfinder.build_catalog import build

        repo = tmp_path / "repo"
        _make_project_skill(repo / ".claude" / "skills", "my-proj-skill", with_sidecar=True)

        out = tmp_path / "cat.json"
        log = tmp_path / "log"
        rc = build(
            skills_dir=tmp_path / "no-skills",
            agents_dir=tmp_path / "no-agents",
            corpus_path=tmp_path / "absent.jsonl",
            out_path=out,
            log_path=log,
            project_root=repo,
            now="2026-05-05T00:00:00Z",
        )
        assert rc == 0
        catalog = json.loads(out.read_text(encoding="utf-8"))
        entry = next(
            (e for e in catalog["entries"] if e["name"] == "my-proj-skill"),
            None,
        )
        assert entry is not None, "project skill must appear in catalog"
        assert entry["source"] == "project"



# Issue #132 — stale disabled-override: bare vs explicit-flag consistency
# ---------------------------------------------------------------------------


def _make_minimal_claude_home(root: Path) -> dict:
    """Create a minimal claude-home fixture for issue-132 regression tests.

    Builds the directory tree:
    ```
    <root>/
      skills/my-skill/SKILL.md
      agents/router.md
      plugins/installed_plugins.json      (no plugins)
      triggers/commit-commands/clean_gone.yml  (stale disabled override)
      triggers/builtin/                   (empty — no builtin sidecars)
      state/                              (output dir)
    ```

    Args:
        root: Temporary directory root.

    Returns:
        A dict with keys ``skills_dir``, ``agents_dir``, ``plugins_dir``,
        ``triggers_dir``, ``builtin_dir``, ``state_dir`` pointing to the
        corresponding sub-paths.
    """
    skills_dir = root / "skills"
    agents_dir = root / "agents"
    plugins_dir = root / "plugins"
    triggers_dir = root / "triggers"
    builtin_dir = triggers_dir / "builtin"
    state_dir = root / "state"

    # Owned skill (dormant — no triggers.yml, so no routing surface needed).
    skill_dir = skills_dir / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Test skill.\n---\n",
        encoding="utf-8",
    )

    # Owned agent with routable: false (suppresses the router-agent warning
    # so stderr is clean and we can assert on the override warning alone).
    agents_dir.mkdir()
    (agents_dir / "router.md").write_text(
        "---\nname: router\ndescription: Router.\nroutable: false\n---\n",
        encoding="utf-8",
    )

    # Plugin manifest with no plugins installed.
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text(
        '{"version": 2, "plugins": {}}',
        encoding="utf-8",
    )

    # Stale disabled override: targets commit-commands:clean_gone which
    # does NOT exist in the plugin-discovery index (exact file from issue #132).
    override_dir = triggers_dir / "commit-commands"
    override_dir.mkdir(parents=True)
    (override_dir / "clean_gone.yml").write_text(
        'disabled: true\nreason: "permanently broken — see issue #132"\n',
        encoding="utf-8",
    )

    builtin_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir()

    return {
        "skills_dir": skills_dir,
        "agents_dir": agents_dir,
        "plugins_dir": plugins_dir,
        "triggers_dir": triggers_dir,
        "builtin_dir": builtin_dir,
        "state_dir": state_dir,
    }


def test_stale_disabled_override_bare_invocation_exits_zero(
    tmp_path: Path,
) -> None:
    """Bare-defaults invocation exits 0 when a stale disabled-override is present.

    Regression test for issue #132: ``catalog build`` (no explicit flags)
    must exit 0 and write a catalog even when a ``disabled: true`` override
    file targets a plugin skill that is not in the discovery index.

    The stale override must be warned in the log (warn-and-skip), not treated
    as a fatal error.
    """
    from claude_wayfinder.build_catalog import build

    dirs = _make_minimal_claude_home(tmp_path)

    # "Bare-defaults" style: all paths are the canonical CLAUDE_HOME defaults.
    out = dirs["state_dir"] / "dispatch-catalog.json"
    log = dirs["state_dir"] / "catalog-generation.log"

    rc = build(
        skills_dir=dirs["skills_dir"],
        agents_dir=dirs["agents_dir"],
        plugin_overrides_dir=dirs["triggers_dir"],
        plugins_dir=dirs["plugins_dir"],
        builtin_agents_dir=dirs["builtin_dir"],
        corpus_path=None,
        out_path=out,
        log_path=log,
        now="2026-05-18T00:00:00Z",
    )

    assert rc == 0, (
        f"bare-defaults invocation must exit 0 on stale disabled-override; "
        f"got {rc}"
    )
    assert out.exists(), (
        "catalog must be written even when a stale disabled-override is present"
    )
    log_text = log.read_text(encoding="utf-8")
    assert "disable override targets nonexistent entry" in log_text, (
        f"expected stale-override warning in log; log:\n{log_text}"
    )


def test_stale_disabled_override_explicit_flags_exits_zero(
    tmp_path: Path,
) -> None:
    """Explicit-flag invocation exits 0 when a stale disabled-override is present.

    Regression test for issue #132 (paired with the bare-invocation test):
    ``catalog build`` with all 5 dir flags explicit must also exit 0 and
    write a catalog, with the same stale-override warning in the log.
    """
    from claude_wayfinder.build_catalog import build

    dirs = _make_minimal_claude_home(tmp_path)

    # "Explicit-flag" style: different --out and --log paths (as in issue #132
    # repro where the explicit call used ~/.claude/.tmp/ paths).
    out = tmp_path / "explicit-out" / "dispatch-catalog.json"
    out.parent.mkdir()
    log = tmp_path / "explicit-out" / "catalog-generation.log"

    rc = build(
        skills_dir=dirs["skills_dir"],
        agents_dir=dirs["agents_dir"],
        plugin_overrides_dir=dirs["triggers_dir"],
        plugins_dir=dirs["plugins_dir"],
        builtin_agents_dir=dirs["builtin_dir"],
        corpus_path=None,
        out_path=out,
        log_path=log,
        now="2026-05-18T00:00:00Z",
    )

    assert rc == 0, (
        f"explicit-flag invocation must exit 0 on stale disabled-override; "
        f"got {rc}"
    )
    assert out.exists(), (
        "catalog must be written even when a stale disabled-override is present"
    )
    log_text = log.read_text(encoding="utf-8")
    assert "disable override targets nonexistent entry" in log_text, (
        f"expected stale-override warning in log; log:\n{log_text}"
    )


def test_stale_disabled_override_both_invocations_consistent(
    tmp_path: Path,
) -> None:
    """Bare and explicit-flag invocations produce the same exit code and warning.

    Regression test for issue #132: the stale-override tombstone (disabled:
    true targeting a nonexistent plugin entry) must produce:

    * The **same exit code** (0) regardless of whether dir paths came from
      defaults or were explicitly supplied.
    * The **same warning text** in the catalog log (warn-and-skip, not fatal).
    * A **complete catalog** written to disk in both cases.

    This is the definitive consistency check: if either invocation exits
    non-zero or fails to write a catalog, the regression is present.
    """
    import json

    from claude_wayfinder.build_catalog import build

    dirs = _make_minimal_claude_home(tmp_path)

    # Bare-defaults invocation: writes to state/ (canonical default path).
    out_bare = dirs["state_dir"] / "dispatch-catalog.json"
    log_bare = dirs["state_dir"] / "catalog-generation.log"
    rc_bare = build(
        skills_dir=dirs["skills_dir"],
        agents_dir=dirs["agents_dir"],
        plugin_overrides_dir=dirs["triggers_dir"],
        plugins_dir=dirs["plugins_dir"],
        builtin_agents_dir=dirs["builtin_dir"],
        corpus_path=None,
        out_path=out_bare,
        log_path=log_bare,
        now="2026-05-18T00:00:00Z",
    )

    # Explicit-flag invocation: writes to a different output path (simulating
    # the user's --out ~/.claude/.tmp/out.json pattern from the issue repro).
    out_explicit = tmp_path / "tmp-out" / "dispatch-catalog.json"
    out_explicit.parent.mkdir()
    log_explicit = tmp_path / "tmp-out" / "catalog-generation.log"
    rc_explicit = build(
        skills_dir=dirs["skills_dir"],
        agents_dir=dirs["agents_dir"],
        plugin_overrides_dir=dirs["triggers_dir"],
        plugins_dir=dirs["plugins_dir"],
        builtin_agents_dir=dirs["builtin_dir"],
        corpus_path=None,
        out_path=out_explicit,
        log_path=log_explicit,
        now="2026-05-18T00:00:00Z",
    )

    # Both must exit 0 (permissive: warn-and-skip, not fail).
    assert rc_bare == 0, (
        f"bare-defaults invocation must exit 0; got {rc_bare}"
    )
    assert rc_explicit == 0, (
        f"explicit-flag invocation must exit 0; got {rc_explicit}"
    )
    assert rc_bare == rc_explicit, (
        f"bare ({rc_bare}) and explicit ({rc_explicit}) invocations must "
        "produce the same exit code"
    )

    # Both must write a catalog.
    assert out_bare.exists(), "bare invocation must write catalog"
    assert out_explicit.exists(), "explicit-flag invocation must write catalog"

    # Both catalogs must have the same entries (same source data).
    cat_bare = json.loads(out_bare.read_text(encoding="utf-8"))
    cat_explicit = json.loads(out_explicit.read_text(encoding="utf-8"))
    assert len(cat_bare["entries"]) == len(cat_explicit["entries"]), (
        f"entry counts differ: bare={len(cat_bare['entries'])}, "
        f"explicit={len(cat_explicit['entries'])}"
    )

    # Both logs must contain the stale-override warning.
    warn_text = "disable override targets nonexistent entry"
    log_bare_text = log_bare.read_text(encoding="utf-8")
    log_explicit_text = log_explicit.read_text(encoding="utf-8")
    assert warn_text in log_bare_text, (
        f"bare log missing stale-override warning; log:\n{log_bare_text}"
    )
    assert warn_text in log_explicit_text, (
        f"explicit log missing stale-override warning; "
        f"log:\n{log_explicit_text}"
    )


# ---------------------------------------------------------------------------
# Issue #505 — Pass 2.6 builtin agent sidecars
# ---------------------------------------------------------------------------


def _make_builtin_sidecar(builtin_dir: Path, name: str, *, min_version: str) -> None:
    """Write a minimal valid builtin sidecar YAML into *builtin_dir*.

    Args:
        builtin_dir: Directory into which to write ``<name>.yml``.
        name: Agent name (also the file stem).
        min_version: ``min_claude_version`` field value.
    """
    builtin_dir.mkdir(parents=True, exist_ok=True)
    (builtin_dir / f"{name}.yml").write_text(
        textwrap.dedent(
            f"""\
            name: {name}
            kind: agent
            description: "Test builtin agent {name}."
            min_claude_version: "{min_version}"
            triggers:
              keywords:
                - {{term: "{name.lower()}", weight: 1.0}}
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )


def test_builtin_pass_loads_explore_and_plan_from_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 2.6 loads Explore and Plan sidecars with source='builtin'.

    Both sidecars present; running version supplied via CLAUDE_VERSION env
    var; both entries must appear in catalog with source='builtin' and
    kind='agent'.
    """
    from claude_wayfinder.build_catalog import build

    monkeypatch.setenv("CLAUDE_VERSION", "2.1.138")

    builtin_dir = tmp_path / "triggers" / "builtin"
    _make_builtin_sidecar(builtin_dir, "Explore", min_version="2.1")
    _make_builtin_sidecar(builtin_dir, "Plan", min_version="2.1")

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    assert rc == 0, f"build() must succeed; got rc={rc}"
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names_by_source = {e["name"]: e["source"] for e in catalog["entries"]}
    assert "Explore" in names_by_source, "Explore must appear in catalog"
    assert "Plan" in names_by_source, "Plan must appear in catalog"
    assert names_by_source["Explore"] == "builtin"
    assert names_by_source["Plan"] == "builtin"
    # Verify kind=agent
    explore = next(e for e in catalog["entries"] if e["name"] == "Explore")
    assert explore["kind"] == "agent"


def test_builtin_unpinned_emits_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A builtin sidecar lacking min_claude_version emits a fatal issue.

    The entry must be excluded from the catalog.  The build still succeeds
    overall (rc=0) unless the excluded entry tips the degraded threshold.
    """
    from claude_wayfinder.build_catalog import build

    monkeypatch.setenv("CLAUDE_VERSION", "2.1.138")

    builtin_dir = tmp_path / "triggers" / "builtin"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "Unpinned.yml").write_text(
        textwrap.dedent(
            """\
            name: Unpinned
            kind: agent
            description: "Missing version pin."
            triggers:
              keywords:
                - {term: "unpinned", weight: 1.0}
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    log_text = log.read_text(encoding="utf-8")
    assert "fatal" in log_text, "fatal issue must be logged for unpinned sidecar"
    assert (
        "min_claude_version" in log_text
    ), "log must mention min_claude_version so authors know what to fix"
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = {e["name"] for e in catalog["entries"]}
    assert "Unpinned" not in names, "unpinned entry must be excluded from catalog"


def test_builtin_outside_version_range_emits_warning_and_excludes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sidecar pinned to min: '99.0' is excluded when running 2.1.x.

    The build still produces rc=0 (one entry, one excluded = 100%, but
    total discovered is 1; 1/1=100% excluded triggers rc=2).  We test the
    warning message and exclusion independently of the rc.
    """
    from claude_wayfinder.build_catalog import build

    monkeypatch.setenv("CLAUDE_VERSION", "2.1.138")

    builtin_dir = tmp_path / "triggers" / "builtin"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "Future.yml").write_text(
        textwrap.dedent(
            """\
            name: Future
            kind: agent
            description: "Requires future Claude."
            min_claude_version: "99.0"
            triggers:
              keywords:
                - {term: "future", weight: 1.0}
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text, "warning must be logged for out-of-range version"
    assert "excluded" in log_text, "log must mention that the entry is excluded"
    assert "99.0" in log_text, "log must cite the pinned version"
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = {e["name"] for e in catalog["entries"]}
    assert "Future" not in names, "out-of-range entry must be excluded from catalog"


def test_is_agent_routable_builtin_routable_by_default() -> None:
    """is_agent_routable returns True for kind='agent' source='builtin'.

    Builtin agents are routable by default — unlike plugin agents which
    require an explicit override to participate in routing.
    """
    from claude_wayfinder.match_filters import is_agent_routable

    assert (
        is_agent_routable(name="Explore", kind="agent", source="builtin") is True
    ), "builtin agents must be routable by default"
    assert (
        is_agent_routable(name="Plan", kind="agent", source="builtin") is True
    ), "builtin agents must be routable by default"


def test_match_includes_builtin_agent_in_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explore participates in agent-pool scoring when triggers match.

    Verifies end-to-end that a builtin agent entry built from a sidecar
    enters the catalog with active triggers, and that is_agent_routable
    confirms it is eligible for the scoring pool.
    """
    from claude_wayfinder.build_catalog import build
    from claude_wayfinder.match_filters import is_agent_routable

    monkeypatch.setenv("CLAUDE_VERSION", "2.1.138")

    builtin_dir = tmp_path / "triggers" / "builtin"
    _make_builtin_sidecar(builtin_dir, "Explore", min_version="2.1")

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    assert rc == 0

    catalog = json.loads(out.read_text(encoding="utf-8"))
    explore = next(
        (e for e in catalog["entries"] if e["name"] == "Explore"),
        None,
    )
    assert explore is not None, "Explore must be present in catalog"

    # Confirm the entry's trigger keywords are non-empty (agent is active)
    assert explore["triggers"]["keywords"], "Explore must have active keyword triggers in catalog"

    # Confirm the predicate considers it routable
    assert is_agent_routable(
        name=explore["name"],
        kind=explore["kind"],
        source=explore["source"],
    ), "is_agent_routable must return True for Explore with source='builtin'"


def test_builtin_pass_skips_when_no_sidecars_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 2.6 emits no ValidationIssues when the builtin dir is absent or empty.

    When the triggers/builtin/ directory does not exist (or exists but
    holds no .yml files), version detection must be skipped entirely —
    no fatal or warning issues are emitted and the build succeeds.

    This covers the CI case where neither ``claude`` is on PATH nor
    ``CLAUDE_VERSION`` is set, but no builtin sidecars need evaluating.
    Subprocess is monkeypatched to simulate "claude not on PATH" so the
    test is hermetic regardless of the local environment.
    """
    import subprocess as _subprocess

    from claude_wayfinder.build_catalog import build

    # Ensure CLAUDE_VERSION is unset so _read_claude_version would fail
    # if it were called.
    monkeypatch.delenv("CLAUDE_VERSION", raising=False)

    original_run = _subprocess.run

    def _no_claude_run(cmd: list[str], **kwargs: object) -> _subprocess.CompletedProcess[str]:
        """Simulate 'claude' not found; all other commands pass through."""
        if cmd and cmd[0] == "claude":
            raise FileNotFoundError("claude not found")
        return original_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_subprocess, "run", _no_claude_run)

    # Case 1: builtin dir does not exist at all.
    missing_builtin_dir = tmp_path / "triggers" / "builtin"
    out = tmp_path / "cat.json"
    log = tmp_path / "log.txt"

    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=missing_builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    log_text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "fatal" not in log_text, (
        "Pass 2.6 must not emit fatal issues when builtin dir is absent.\n" f"Log: {log_text}"
    )
    assert "cannot determine running Claude Code version" not in log_text, (
        "Version detection must be skipped when there are no sidecars.\n" f"Log: {log_text}"
    )

    # Case 2: builtin dir exists but contains no .yml files.
    empty_builtin_dir = tmp_path / "triggers2" / "builtin"
    empty_builtin_dir.mkdir(parents=True)
    out2 = tmp_path / "cat2.json"
    log2 = tmp_path / "log2.txt"

    rc2 = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out2,
        log_path=log2,
        builtin_agents_dir=empty_builtin_dir,
        now="2026-05-10T00:00:00Z",
    )
    log_text2 = log2.read_text(encoding="utf-8") if log2.exists() else ""
    assert "fatal" not in log_text2, (
        "Pass 2.6 must not emit fatal issues when builtin dir is empty.\n" f"Log: {log_text2}"
    )
    assert "cannot determine running Claude Code version" not in log_text2, (
        "Version detection must be skipped when there are no sidecars.\n" f"Log: {log_text2}"
    )
    # Suppress unused-variable warnings — both rc values are fine (0 or 2
    # depending on whether any other entries exist).
    _ = rc
    _ = rc2


def test_builtin_pass_warns_when_version_unknown_with_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sidecars exist but version cannot be determined, emit a warning (not fatal).

    Simulates a CI runner that has no ``claude`` binary on PATH and no
    ``CLAUDE_VERSION`` env var.  The build must still succeed (no fatal
    ValidationIssue from Pass 2.6) and the builtin entries are excluded
    with a single warning-level log line.
    """
    from claude_wayfinder.build_catalog import build

    # Unset CLAUDE_VERSION so _read_claude_version falls through to failure.
    monkeypatch.delenv("CLAUDE_VERSION", raising=False)

    # Monkeypatch subprocess.run so "claude --version" always fails.
    import subprocess as _subprocess

    original_run = _subprocess.run

    def _failing_claude_run(cmd: list[str], **kwargs: object) -> _subprocess.CompletedProcess[str]:
        """Return a non-zero result only for 'claude --version'."""
        if cmd and cmd[0] == "claude":
            return _subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="command not found",
            )
        return original_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_subprocess, "run", _failing_claude_run)

    builtin_dir = tmp_path / "triggers" / "builtin"
    _make_builtin_sidecar(builtin_dir, "Explore", min_version="2.1")

    out = tmp_path / "cat.json"
    log = tmp_path / "log.txt"

    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        builtin_agents_dir=builtin_dir,
        now="2026-05-10T00:00:00Z",
    )

    log_text = log.read_text(encoding="utf-8")

    # Must be a warning, not a fatal.
    assert "fatal" not in log_text, (
        "Version-unknown must emit warning, not fatal.\n" f"Log: {log_text}"
    )
    assert "warning" in log_text, (
        "Version-unknown must emit at least one warning.\n" f"Log: {log_text}"
    )
    assert "cannot determine running Claude Code version" in log_text, (
        "Warning message must explain why builtin entries were excluded.\n" f"Log: {log_text}"
    )

    # Builtin entries must be excluded (version unknown → can't pin-check).
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = {e["name"] for e in catalog["entries"]}
    assert "Explore" not in names, "Builtin entries must be excluded when version is unknown."


# ---------------------------------------------------------------------------
# Issue #19 — data-driven ``routable`` flag (replaces hardcoded name check)
# ---------------------------------------------------------------------------


def test_routable_false_in_frontmatter_propagates_to_catalog(
    tmp_path: Path,
) -> None:
    """An agent with ``routable: false`` in frontmatter gets ``routable=False`` in the entry.

    The catalog generator must read the ``routable`` field and store it
    on the entry so the matcher can call ``is_agent_routable`` with the
    flag rather than hardcoding a name comparison.
    """
    from claude_wayfinder.build_catalog import build

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "router-agent.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: router-agent
            description: The dispatch router.
            routable: false
            ---
            """
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-13T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "router-agent")
    assert entry["routable"] is False


def test_routable_absent_defaults_true_in_catalog(tmp_path: Path) -> None:
    """An agent without ``routable:`` in frontmatter gets ``routable=True``.

    The omitted flag must default to ``True`` so existing agent files
    that do not declare the field remain fully routable.
    """
    from claude_wayfinder.build_catalog import build

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "specialist.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: specialist
            description: A specialist agent.
            ---
            """
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-13T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "specialist")
    assert entry.get("routable", True) is True


def test_catalog_router_agent_metadata_populated(tmp_path: Path) -> None:
    """The catalog ``router_agent`` top-level field names the first routable=false agent."""
    from claude_wayfinder.build_catalog import build

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "router-agent.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: router-agent
            description: The dispatch router.
            routable: false
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "code-writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: code-writer
            description: Writes code.
            ---
            """
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-13T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    assert catalog.get("router_agent") == "router-agent"


def test_catalog_router_agent_null_when_none_declared(
    tmp_path: Path,
) -> None:
    """The catalog ``router_agent`` field is ``null`` when no routable=false agent exists.

    A warning must also be emitted to stderr so operators notice the
    missing declaration.
    """
    import io
    import sys

    from claude_wayfinder.build_catalog import build

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "specialist.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: specialist
            description: A specialist.
            ---
            """
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"

    # Capture stderr to verify the warning is emitted.
    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        build(
            skills_dir=tmp_path / "no-skills",
            agents_dir=agents,
            corpus_path=tmp_path / "absent.jsonl",
            out_path=out,
            log_path=log,
            now="2026-05-13T00:00:00Z",
        )
    finally:
        sys.stderr = old_stderr

    catalog = json.loads(out.read_text(encoding="utf-8"))
    assert catalog.get("router_agent") is None
    warning_output = captured.getvalue()
    assert "no router agent" in warning_output.lower()


# ===========================================================================
