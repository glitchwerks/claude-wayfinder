"""Tests for claude_wayfinder.build_catalog._discover — I/O and discovery.

Covers:
  - load_frontmatter (top-level helpers)
  - v6 sidecar schema — load_trigger_sidecar, discover_plugin_overrides, etc.
    (Issue #250)
  - content_hash + revision sidecar (Task 1 / Issue #395)
  - Plugin discovery functions (Issue #476)
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml

from claude_wayfinder.build_catalog import (
    build,
    compute_content_hash,
    load_frontmatter,
    load_trigger_sidecar,
    update_revisions_sidecar,
)

FIXTURES = Path(__file__).parent / "fixtures"

def test_load_frontmatter_extracts_yaml_block(tmp_path: Path) -> None:
    """Reads the YAML block between leading and trailing '---' fences."""
    f = tmp_path / "SKILL.md"
    f.write_text(
        textwrap.dedent(
            """\
            ---
            name: test-skill
            description: A test.
            triggers:
              keywords:
                - {term: "foo", weight: 1.0}
            ---
            # Body content (ignored)
            """
        ),
        encoding="utf-8",
    )
    fm = load_frontmatter(f)
    assert fm["name"] == "test-skill"
    assert fm["triggers"]["keywords"][0]["term"] == "foo"


def test_load_frontmatter_returns_none_when_no_fence(tmp_path: Path) -> None:
    """Returns None when the file has no leading --- fence."""
    f = tmp_path / "SKILL.md"
    f.write_text("no frontmatter here\n", encoding="utf-8")
    assert load_frontmatter(f) is None


def test_load_frontmatter_raises_on_bad_yaml(tmp_path: Path) -> None:
    """Raises yaml.YAMLError when the fenced block contains malformed YAML."""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: [unclosed\n---\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_frontmatter(f)



# ---------------------------------------------------------------------------
# Issue #250 — v6 sidecar schema tests
# ---------------------------------------------------------------------------



def test_load_trigger_sidecar_missing_returns_none(tmp_path: Path) -> None:
    """Returns None when no triggers.yml file exists in the directory."""
    sidecar = tmp_path / "triggers.yml"
    assert not sidecar.exists()
    result = load_trigger_sidecar(tmp_path)
    assert result is None


def test_load_trigger_sidecar_empty_returns_none(tmp_path: Path) -> None:
    """Returns None when triggers.yml exists but is empty."""
    sidecar = tmp_path / "triggers.yml"
    sidecar.write_text("", encoding="utf-8")
    result = load_trigger_sidecar(tmp_path)
    assert result is None


def test_load_trigger_sidecar_valid_returns_dict(tmp_path: Path) -> None:
    """Returns a parsed dict when triggers.yml contains valid YAML."""
    sidecar = tmp_path / "triggers.yml"
    sidecar.write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "csv", weight: 1.0 }
            applicable_agents: ["code-writer"]
            """
        ),
        encoding="utf-8",
    )
    result = load_trigger_sidecar(tmp_path)
    assert result is not None
    assert isinstance(result, dict)
    assert "triggers" in result
    assert result["triggers"]["keywords"][0]["term"] == "csv"


def test_load_trigger_sidecar_parse_error_returns_none_with_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Returns None when triggers.yml is malformed YAML; logs a warning."""
    sidecar = tmp_path / "triggers.yml"
    sidecar.write_text("triggers: [unclosed\n", encoding="utf-8")
    import logging

    with caplog.at_level(logging.WARNING):
        result = load_trigger_sidecar(tmp_path)
    assert result is None
    assert any("parse" in r.message.lower() or "yaml" in r.message.lower() for r in caplog.records)


def test_discover_plugin_overrides_finds_namespaced_entries(tmp_path: Path) -> None:
    """walk triggers/<plugin>/<skill>.yml yields plugin-namespaced entries."""
    from claude_wayfinder.build_catalog import discover_plugin_overrides

    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "brainstorm", weight: 1.0 }
            applicable_agents: ["*"]
            """
        ),
        encoding="utf-8",
    )
    ms_dir = triggers_root / "microsoft-docs"
    ms_dir.mkdir(parents=True)
    (ms_dir / "microsoft-skill-creator.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "microsoft", weight: 1.0 }
            applicable_agents: ["code-writer"]
            """
        ),
        encoding="utf-8",
    )
    entries = discover_plugin_overrides(triggers_root)
    names = {name for _kind, name, _sidecar in entries}
    assert "superpowers:brainstorming" in names
    assert "microsoft-docs:microsoft-skill-creator" in names


def test_plugin_override_entry_has_source_tag(tmp_path: Path) -> None:
    """Plugin-override catalog entries carry source='plugin-override'."""

    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "brainstorm", weight: 1.0 }
            applicable_agents: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "superpowers:brainstorming")
    assert entry["source"] == "plugin-override"


def test_owned_skill_entry_has_source_tag(tmp_path: Path) -> None:
    """Owned skill catalog entries carry source='owned'."""

    skills = tmp_path / "skills"
    s = skills / "csv-utils"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: csv-utils\ndescription: CSV helpers.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "csv", weight: 1.0 }
            applicable_agents: ["code-writer"]
            """
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=skills,
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "csv-utils")
    assert entry["source"] == "owned"


def test_skill_md_with_leftover_triggers_block_is_warned_and_ignored(
    tmp_path: Path,
) -> None:
    """SKILL.md triggers/applicable_agents blocks are ignored; sidecar wins.

    This is the v5->v6 migration safety net.  If a SKILL.md still contains
    old inline triggers the generator must warn and use the sidecar (or
    treat as dormant when no sidecar exists).
    """

    skills = tmp_path / "skills"
    s = skills / "leftover"
    s.mkdir(parents=True)
    # SKILL.md still has inline triggers (v5 style) — these must be ignored
    (s / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: leftover
            description: Has leftover v5 frontmatter.
            triggers:
              keywords:
                - { term: "should-be-ignored", weight: 1.0 }
            applicable_agents: ["code-writer"]
            ---
            """
        ),
        encoding="utf-8",
    )
    # No sidecar — entry should be dormant
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=skills,
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text
    assert "leftover" in log_text
    # The inline triggers must NOT appear in the catalog entry
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "leftover")
    assert entry["triggers"]["keywords"] == []


def test_skill_with_no_sidecar_is_dormant(tmp_path: Path) -> None:
    """A skill dir with SKILL.md but no triggers.yml produces a dormant entry."""

    skills = tmp_path / "skills"
    s = skills / "plain-skill"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: plain-skill\ndescription: No sidecar.\n---\n",
        encoding="utf-8",
    )
    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=skills,
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "plain-skill")
    # ``file_extensions`` was deprecated in Issue #249; it must not appear
    # in catalog entries even for dormant skills.
    assert "file_extensions" not in entry["triggers"]
    for field in (
        "keywords",
        "command_prefixes",
        "agent_mentions",
        "path_globs",
        "tool_mentions",
        "excludes",
    ):
        assert entry["triggers"][field] == []


def test_e2e_v6_pilot_python_active_after_migration(tmp_path: Path) -> None:
    """End-to-end: python skill with sidecar is active in catalog (source=owned).

    Mirrors the real pilot migration: SKILL.md holds only runtime fields,
    triggers.yml holds the trigger config.  The resulting catalog entry
    must be active (non-empty keywords) and carry source='owned'.
    """

    skills = tmp_path / "skills"
    p = skills / "python"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: python
            description: Expert Python code writing.
            ---
            """
        ),
        encoding="utf-8",
    )
    (p / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              path_globs:
                - "**/*.py"
              keywords:
                - { term: "python", weight: 1.0 }
                - { term: "pytest", weight: 1.0 }
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
        plugin_overrides_dir=tmp_path / "no-triggers",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "python")
    assert entry["source"] == "owned"
    terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "python" in terms
    assert entry["applicable_agents"] == ["*"]



# ---------------------------------------------------------------------------
# Task 1 (Issue #395) — content_hash + revision sidecar
# ---------------------------------------------------------------------------


def test_compute_content_hash_returns_12_char_hex(tmp_path: Path) -> None:
    """compute_content_hash returns a 12-character lowercase hex string."""
    f = tmp_path / "a.md"
    f.write_text("hello\n")
    h = compute_content_hash(f)
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_content_hash_deterministic_for_same_bytes(tmp_path: Path) -> None:
    """Two files with identical bytes produce the same 12-char hash."""
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("body\n")
    b.write_text("body\n")
    assert compute_content_hash(a) == compute_content_hash(b)


def test_update_revisions_sidecar_creates_on_first_run(tmp_path: Path) -> None:
    """Sidecar is created from scratch and all components land at rev=1."""
    sidecar = tmp_path / "component-revisions.json"
    components = [
        {"name": "code-writer", "kind": "agent", "content_hash": "aaa111"},
        {"name": "dispatch", "kind": "skill", "content_hash": "bbb222"},
    ]
    update_revisions_sidecar(components, sidecar)
    data = json.loads(sidecar.read_text())
    assert data["components"]["agent:code-writer"]["rev"] == 1
    assert data["components"]["agent:code-writer"]["content_hash"] == "aaa111"
    assert data["components"]["skill:dispatch"]["rev"] == 1


def test_update_revisions_sidecar_increments_on_hash_change(tmp_path: Path) -> None:
    """Rev increments from 1 to 2 when the content_hash changes."""
    sidecar = tmp_path / "component-revisions.json"
    update_revisions_sidecar(
        [{"name": "code-writer", "kind": "agent", "content_hash": "aaa111"}], sidecar
    )
    update_revisions_sidecar(
        [{"name": "code-writer", "kind": "agent", "content_hash": "ccc333"}], sidecar
    )
    data = json.loads(sidecar.read_text())
    assert data["components"]["agent:code-writer"]["rev"] == 2
    assert data["components"]["agent:code-writer"]["content_hash"] == "ccc333"


def test_update_revisions_sidecar_stable_when_hash_unchanged(tmp_path: Path) -> None:
    """Rev stays at 1 across three identical rebuilds (no spurious bumps)."""
    sidecar = tmp_path / "component-revisions.json"
    components = [{"name": "code-writer", "kind": "agent", "content_hash": "aaa111"}]
    update_revisions_sidecar(components, sidecar)
    update_revisions_sidecar(components, sidecar)
    update_revisions_sidecar(components, sidecar)
    data = json.loads(sidecar.read_text())
    assert data["components"]["agent:code-writer"]["rev"] == 1


def test_update_revisions_sidecar_handles_new_components(tmp_path: Path) -> None:
    """A component added on the second rebuild lands at rev=1."""
    sidecar = tmp_path / "component-revisions.json"
    update_revisions_sidecar(
        [{"name": "old-skill", "kind": "skill", "content_hash": "111"}], sidecar
    )
    update_revisions_sidecar(
        [
            {"name": "old-skill", "kind": "skill", "content_hash": "111"},
            {"name": "new-skill", "kind": "skill", "content_hash": "222"},
        ],
        sidecar,
    )
    data = json.loads(sidecar.read_text())
    assert data["components"]["skill:old-skill"]["rev"] == 1
    assert data["components"]["skill:new-skill"]["rev"] == 1


# ---------------------------------------------------------------------------
# Issue #476 — plugin discovery functions
# ---------------------------------------------------------------------------


def _write_manifest(
    plugins_root: Path,
    version: int,
    plugins: dict,
) -> Path:
    """Write a synthetic installed_plugins.json into *plugins_root*.

    Args:
        plugins_root: Directory to create the manifest in.
        version: Top-level ``version`` field value.
        plugins: Mapping of plugin keys to their install-entry arrays.

    Returns:
        Path to the created manifest file.
    """
    plugins_root.mkdir(parents=True, exist_ok=True)
    manifest_path = plugins_root / "installed_plugins.json"
    manifest_path.write_text(
        json.dumps({"version": version, "plugins": plugins}),
        encoding="utf-8",
    )
    return manifest_path


def test_discover_installed_plugins_happy_path(tmp_path: Path) -> None:
    """Returns one install tuple per valid user-scoped plugin entry.

    The manifest has two plugins, both with scope='user' and real
    installPath directories.  The function should return both tuples in
    sorted order (by plugin key).
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    alpha_path = tmp_path / "alpha"
    beta_path = tmp_path / "beta"
    alpha_path.mkdir()
    beta_path.mkdir()

    _write_manifest(
        tmp_path,
        version=2,
        plugins={
            "beta@marketplace": [
                {"scope": "user", "installPath": str(beta_path), "version": "1.0"}
            ],
            "alpha@marketplace": [
                {"scope": "user", "installPath": str(alpha_path), "version": "2.0"}
            ],
        },
    )

    sink: list = []
    result = discover_installed_plugins(tmp_path, sink)
    assert sink == [], f"no issues expected, got: {sink}"
    assert len(result) == 2
    # Sorted by key: alpha@marketplace before beta@marketplace
    names = [r[0] for r in result]
    assert names == ["alpha@marketplace", "beta@marketplace"]
    assert result[0][1] == "2.0"
    assert result[0][2] == alpha_path


def test_discover_installed_plugins_missing_manifest_returns_empty_no_issue(
    tmp_path: Path,
) -> None:
    """Returns empty list and emits an info issue when manifest is absent.

    A missing manifest is an expected state (no plugins installed), so
    the function emits ``info`` severity, not a warning.
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    sink: list = []
    result = discover_installed_plugins(tmp_path / "nonexistent", sink)
    assert result == []
    assert len(sink) == 1
    assert sink[0].severity == "info"


def test_discover_installed_plugins_unsupported_version_returns_empty_with_warning_in_sink(
    tmp_path: Path,
) -> None:
    """Returns empty list and emits warning for manifest version < 2.

    Version 1 (and absent version) are not supported.  The function must
    warn and bail rather than attempting to parse an unknown schema.
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    _write_manifest(tmp_path, version=1, plugins={})

    sink: list = []
    result = discover_installed_plugins(tmp_path, sink)
    assert result == []
    assert any(i.severity == "warning" for i in sink)


def test_discover_installed_plugins_forward_compat_v3_accepted(
    tmp_path: Path,
) -> None:
    """Version >= 2 (e.g. v3) is accepted as forward-compatible.

    The spec says accept ``version >= 2`` for forward-compatibility with
    future manifest supersets.  A v3 manifest with valid user-scoped entries
    must succeed without emitting any warning issues.
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    install_path = tmp_path / "plugin-v3"
    install_path.mkdir()

    _write_manifest(
        tmp_path,
        version=3,
        plugins={
            "fancy@marketplace": [
                {"scope": "user", "installPath": str(install_path), "version": "9.0"}
            ],
        },
    )

    sink: list = []
    result = discover_installed_plugins(tmp_path, sink)
    warnings = [i for i in sink if i.severity == "warning"]
    assert warnings == [], f"no warnings expected for v3 manifest, got: {warnings}"
    assert len(result) == 1
    assert result[0][0] == "fancy@marketplace"


def test_discover_installed_plugins_skips_missing_installpath_with_warning(
    tmp_path: Path,
) -> None:
    """Skips an entry whose installPath does not exist on disk; emits warning.

    The plugin entry is well-formed but the directory it points to was
    removed.  The function should skip that entry, emit a warning, and
    still return any other valid entries.
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    real_path = tmp_path / "real"
    real_path.mkdir()
    ghost_path = tmp_path / "ghost"  # intentionally NOT created

    _write_manifest(
        tmp_path,
        version=2,
        plugins={
            "real@mkt": [{"scope": "user", "installPath": str(real_path), "version": "1.0"}],
            "ghost@mkt": [{"scope": "user", "installPath": str(ghost_path), "version": "1.0"}],
        },
    )

    sink: list = []
    result = discover_installed_plugins(tmp_path, sink)
    names = [r[0] for r in result]
    assert "real@mkt" in names
    assert "ghost@mkt" not in names
    assert any(i.severity == "warning" for i in sink)


def test_discover_installed_plugins_deterministic_under_dict_shuffle(
    tmp_path: Path,
) -> None:
    """Two calls with same manifest produce identical, sorted result lists.

    Plugin discovery must not depend on dict iteration order.  Both calls
    must return the same sequence of tuples.
    """
    from claude_wayfinder.build_catalog import discover_installed_plugins

    paths = {}
    for name in ("zz", "mm", "aa"):
        p = tmp_path / name
        p.mkdir()
        paths[name] = p

    _write_manifest(
        tmp_path,
        version=2,
        plugins={
            "zz@mkt": [{"scope": "user", "installPath": str(paths["zz"]), "version": "1"}],
            "mm@mkt": [{"scope": "user", "installPath": str(paths["mm"]), "version": "1"}],
            "aa@mkt": [{"scope": "user", "installPath": str(paths["aa"]), "version": "1"}],
        },
    )

    sink1: list = []
    sink2: list = []
    result1 = discover_installed_plugins(tmp_path, sink1)
    result2 = discover_installed_plugins(tmp_path, sink2)

    assert result1 == result2
    plugin_names = [r[0] for r in result1]
    assert plugin_names == sorted(plugin_names), "results must be in sorted order"


# ---------------------------------------------------------------------------
# Issue #476 — discover_plugin_entries
# ---------------------------------------------------------------------------


def test_discover_plugin_entries_finds_skills_and_agents(
    tmp_path: Path,
) -> None:
    """Globs SKILL.md and agent *.md files from an install list.

    Creates a synthetic plugin install with one skill and one agent,
    then confirms both are returned with the correct kind tag.
    """
    from claude_wayfinder.build_catalog import discover_plugin_entries

    plugin_dir = tmp_path / "my-plugin"
    skill_dir = plugin_dir / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")

    agents_dir = plugin_dir / "agents"
    agents_dir.mkdir()
    (agents_dir / "my-agent.md").write_text("# agent\n", encoding="utf-8")

    installs = [("my-plugin@mkt", "1.0", plugin_dir)]
    result = discover_plugin_entries(installs)

    kinds_names = [(kind, path.name) for kind, _plugin, path in result]
    assert ("skill", "SKILL.md") in kinds_names
    assert ("agent", "my-agent.md") in kinds_names


def test_discover_plugin_entries_returns_sorted(tmp_path: Path) -> None:
    """Returned list is sorted so catalog generation is deterministic."""
    from claude_wayfinder.build_catalog import discover_plugin_entries

    plugin_dir = tmp_path / "plug"
    for letter in ("z-skill", "a-skill"):
        d = plugin_dir / "skills" / letter
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("", encoding="utf-8")

    installs = [("plug@mkt", "1.0", plugin_dir)]
    result = discover_plugin_entries(installs)

    paths = [str(p) for _, _, p in result]
    assert paths == sorted(paths)


