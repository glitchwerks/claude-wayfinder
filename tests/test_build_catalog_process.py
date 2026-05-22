"""Tests for claude_wayfinder.build_catalog._process — entry processing.

Covers:
  - Plugin-namespaced skill references (Issue #358)
  - Pass 2.5 plugin discovery wired into build() (Issue #477)
  - Finding #3 regression: description: null
  - Finding #2 regression: invalid plugin kind
  - _process_plugin_override + collision-merge (Issue #478)
  - Plugin-agent sidecar overrides (Issue #140)
  - Colocated owned/project agent sidecar overrides (Issue #148)
  - _apply_colocated_sidecars validation and routable=True (Issue #151 + #153)
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from claude_wayfinder.build_catalog import (
    _resolve_applicable_references,
    build,
)

# ---------------------------------------------------------------------------
# Issue #358 — plugin-namespaced skill references in applicable_skills
# ---------------------------------------------------------------------------


def test_plugin_namespaced_skill_kept_in_applicable_skills(
    tmp_path: Path,
) -> None:
    """Agent declaring a plugin-namespaced skill keeps it in the catalog.

    A name like 'microsoft-docs:microsoft-docs' uses the '<plugin>:<skill>'
    format.  It refers to a skill provided by an installed plugin at runtime
    and cannot be verified at catalog-build time.  The entry must be kept
    (not dropped) and an info log must be emitted documenting the
    external-reference bypass.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "devops.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: devops
            description: Infrastructure design consultant.
            triggers:
              keywords:
                - {term: "infrastructure", weight: 1.0}
            applicable_skills:
              - "azure"
              - "microsoft-docs:microsoft-docs"
            ---
            """
        ),
        encoding="utf-8",
    )
    skills = tmp_path / "skills"
    az = skills / "azure"
    az.mkdir(parents=True)
    (az / "SKILL.md").write_text(
        "---\nname: azure\ndescription: Azure skill.\n---\n",
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
        now="2026-05-04T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    agent = next(e for e in catalog["entries"] if e["name"] == "devops")
    # Plugin-namespaced skill must be present, NOT dropped
    assert (
        "microsoft-docs:microsoft-docs" in agent["applicable_skills"]
    ), "plugin-namespaced skill was silently dropped — should be kept"
    # Owned skill must also be present
    assert "azure" in agent["applicable_skills"]
    # No 'dropped' warning for the plugin skill
    log_text = log.read_text(encoding="utf-8")
    assert "microsoft-docs:microsoft-docs' — dropped" not in log_text


def test_plugin_namespaced_skill_not_treated_as_unknown(
    tmp_path: Path,
) -> None:
    """_resolve_applicable_references must not warn+drop plugin-namespaced names.

    The '<plugin>:<skill>' pattern is an external reference to a
    runtime-installed plugin skill.  It must pass through the reference
    resolver without triggering the 'unknown name ... dropped' warning.
    """

    entries = [
        {
            "name": "devops",
            "kind": "agent",
            "description": "",
            "triggers": {},
            "applicable_skills": [
                "python",
                "microsoft-docs:microsoft-docs",
                "superpowers:brainstorming",
            ],
        },
        {
            "name": "python",
            "kind": "skill",
            "description": "",
            "triggers": {},
            "applicable_agents": [],
        },
    ]
    issues: list = []
    _resolve_applicable_references(entries, issues)

    devops_entry = next(e for e in entries if e["name"] == "devops")
    # Plugin-namespaced skills must survive the resolution pass
    assert "microsoft-docs:microsoft-docs" in devops_entry["applicable_skills"]
    assert "superpowers:brainstorming" in devops_entry["applicable_skills"]
    # 'python' is a known owned skill and must also survive
    assert "python" in devops_entry["applicable_skills"]
    # No 'dropped' warnings for plugin-namespaced names
    dropped_warnings = [i for i in issues if "dropped" in i.message]
    assert (
        not dropped_warnings
    ), f"unexpected dropped-warnings for plugin skills: {dropped_warnings}"


def test_devops_agent_plugin_skill_roundtrip(tmp_path: Path) -> None:
    """The real devops agent's 'microsoft-docs:microsoft-docs' roundtrips.

    This is the concrete regression test for issue #358.  Uses the actual
    devops agent frontmatter pattern (inline triggers + applicable_skills
    containing a plugin-namespaced entry) and verifies the entry survives
    catalog generation end-to-end.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    # Mirrors the real devops.md applicable_skills block
    (agents / "devops.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: devops
            description: "Infrastructure design consultant."
            triggers:
              keywords:
                - {term: "infrastructure", weight: 1.0}
                - {term: "deployment", weight: 1.0}
            applicable_skills:
              - "azure"
              - "bicep"
              - "github-actions"
              - "powershell"
              - "python"
              - "microsoft-docs:microsoft-docs"
            ---
            """
        ),
        encoding="utf-8",
    )
    skills = tmp_path / "skills"
    for skill_name in ("azure", "bicep", "github-actions", "powershell", "python"):
        d = skills / skill_name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: {skill_name} skill.\n---\n",
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
        now="2026-05-04T00:00:00Z",
    )
    assert rc == 0, "catalog generation must succeed"
    catalog = json.loads(out.read_text(encoding="utf-8"))
    devops = next(e for e in catalog["entries"] if e["name"] == "devops")
    applicable = devops["applicable_skills"]
    # All owned skills must be present
    for owned in ("azure", "bicep", "github-actions", "powershell", "python"):
        assert owned in applicable, f"owned skill '{owned}' missing from applicable_skills"
    # Plugin skill must round-trip
    assert (
        "microsoft-docs:microsoft-docs" in applicable
    ), "'microsoft-docs:microsoft-docs' was dropped — issue #358 regression"
    # No spurious dropped-warnings in the log
    log_text = log.read_text(encoding="utf-8")
    assert "microsoft-docs:microsoft-docs' — dropped" not in log_text


def test_build_warns_on_unknown_applicable_skill_for_agent(
    tmp_path: Path,
) -> None:
    """Agent referencing a non-existent skill emits a warning and drops."""

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: writer
            description: writes
            triggers:
              keywords:
                - {term: "implement", weight: 1.0}
            applicable_skills: ["python", "ghost-skill"]
            ---
            """
        ),
        encoding="utf-8",
    )
    skills = tmp_path / "skills"
    p = skills / "python"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        "---\nname: python\ndescription: py\n---\n",
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
    assert "applicable_skills references unknown name 'ghost-skill' — dropped" in log.read_text(
        encoding="utf-8"
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    agent = next(e for e in catalog["entries"] if e["name"] == "writer")
    assert agent["applicable_skills"] == ["python"]



# ---------------------------------------------------------------------------
# Issue #477 — Pass 2.5 plugin discovery wired into build()
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
    import json

    plugins_root.mkdir(parents=True, exist_ok=True)
    manifest_path = plugins_root / "installed_plugins.json"
    manifest_path.write_text(
        json.dumps({"version": version, "plugins": plugins}),
        encoding="utf-8",
    )
    return manifest_path


def _make_plugin_install(
    root: Path,
    plugin_name: str,
    *,
    skills: list[str] | None = None,
    agents: list[str] | None = None,
) -> Path:
    """Create a synthetic plugin install directory with skills and agents.

    Writes a valid ``installed_plugins.json`` manifest at *root* pointing
    to the newly created install dir.  Each skill gets a ``SKILL.md``
    with a ``name`` frontmatter field; each agent gets a ``.md`` file.

    Args:
        root: Temp directory in which to create the plugin install.
        plugin_name: Plugin identifier (e.g. ``"superpowers@mkt"``).
        skills: List of skill names to create under ``skills/<name>/``.
        agents: List of agent names to create under ``agents/``.

    Returns:
        Path to the created plugin install directory.
    """
    install_dir = root / plugin_name.replace("@", "_")
    for skill_name in skills or []:
        skill_dir = install_dir / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {plugin_name.split('@')[0]}:{skill_name}\n"
            f"description: Plugin skill {skill_name}.\n---\n",
            encoding="utf-8",
        )
    for agent_name in agents or []:
        agent_dir = install_dir / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / f"{agent_name}.md").write_text(
            f"---\nname: {plugin_name.split('@')[0]}:{agent_name}\n"
            f"description: Plugin agent {agent_name}.\n---\n",
            encoding="utf-8",
        )
    _write_manifest(
        root,
        version=2,
        plugins={
            plugin_name: [{"scope": "user", "installPath": str(install_dir), "version": "1.0"}]
        },
    )
    return install_dir


def test_plugin_skill_emitted_dormant(tmp_path: Path) -> None:
    """Pass 2.5: a plugin skill is emitted as a dormant entry with source='plugin'.

    A plugin skill has no triggers.yml, so it lands in the catalog with
    all trigger lists empty (dormant).  Its name follows the '<plugin>:<skill>'
    convention and its source tag is 'plugin'.
    """

    plugin_root = tmp_path / "plugins"
    plugin_name = "superpowers@mkt"
    _make_plugin_install(plugin_root, plugin_name, skills=["brainstorming"])

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:brainstorming"),
        None,
    )
    assert entry is not None, "plugin skill entry missing from catalog"
    assert entry["source"] == "plugin", f"expected source='plugin', got {entry['source']!r}"
    # Dormant: all trigger lists must be empty
    for field in (
        "keywords",
        "command_prefixes",
        "agent_mentions",
        "path_globs",
        "tool_mentions",
        "excludes",
    ):
        assert (
            entry["triggers"][field] == []
        ), f"trigger field '{field}' should be [] for dormant entry"


def test_plugin_agent_emitted_dormant(tmp_path: Path) -> None:
    """Pass 2.5: a plugin agent is emitted as a dormant entry with source='plugin'.

    A plugin agent has inline frontmatter but no triggers block, so it
    lands dormant.  Its name follows the '<plugin>:<agent>' convention
    and its source tag is 'plugin'.
    """

    plugin_root = tmp_path / "plugins"
    plugin_name = "myplugin@mkt"
    _make_plugin_install(plugin_root, plugin_name, agents=["my-agent"])

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "myplugin:my-agent"),
        None,
    )
    assert entry is not None, "plugin agent entry missing from catalog"
    assert entry["source"] == "plugin", f"expected source='plugin', got {entry['source']!r}"
    # Dormant: all trigger lists must be empty
    for field in (
        "keywords",
        "command_prefixes",
        "agent_mentions",
        "path_globs",
        "tool_mentions",
        "excludes",
    ):
        assert (
            entry["triggers"][field] == []
        ), f"trigger field '{field}' should be [] for dormant entry"


def test_catalog_stability_under_plugin_manifest_key_shuffle(
    tmp_path: Path,
) -> None:
    """Catalog output is byte-identical across two build() calls.

    The plugin manifest's key order does not affect the generated catalog.
    We simulate a shuffle by calling build() twice with the same inputs;
    since dict order is insertion-order in CPython 3.7+, the JSON manifest
    key ordering is deterministic but our implementation must not rely on
    it.  Two consecutive build() calls must produce byte-identical output.
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "beta@mkt", skills=["skill-b"])
    # Overwrite the manifest to add alpha@mkt before beta@mkt
    _write_manifest(
        plugin_root,
        version=2,
        plugins={
            "alpha@mkt": [
                {
                    "scope": "user",
                    "installPath": str(plugin_root / "alpha_mkt"),
                    "version": "1.0",
                }
            ],
            "beta@mkt": [
                {
                    "scope": "user",
                    "installPath": str(plugin_root / "beta_mkt"),
                    "version": "1.0",
                }
            ],
        },
    )
    # Create the alpha install dir with a skill
    alpha_dir = plugin_root / "alpha_mkt"
    skill_a = alpha_dir / "skills" / "skill-a"
    skill_a.mkdir(parents=True, exist_ok=True)
    (skill_a / "SKILL.md").write_text(
        "---\nname: alpha:skill-a\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )

    out1 = tmp_path / "cat1.json"
    out2 = tmp_path / "cat2.json"

    common_kwargs: dict = dict(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=tmp_path / "no-triggers",
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        log_path=tmp_path / "log",
        now="2026-05-09T00:00:00Z",
    )
    rc1 = build(out_path=out1, **common_kwargs)
    rc2 = build(out_path=out2, **common_kwargs)

    assert rc1 == 0
    assert rc2 == 0
    assert (
        out1.read_bytes() == out2.read_bytes()
    ), "catalog output is not byte-stable across two consecutive builds"


def test_resolve_applicable_references_no_info_log_for_known_plugin_skill(
    tmp_path: Path,
) -> None:
    """Post-Pass-2.5: known plugin skills don't emit 'kept as external reference'.

    Before Pass 2.5, plugin skill names like 'superpowers:writing-plans'
    appeared in agents' applicable_skills lists but had no corresponding
    catalog entry, so _resolve_applicable_references logged them at
    info level as 'kept as external reference (unverified at build time)'.

    After Pass 2.5, these skills DO have catalog entries (dormant, with
    source='plugin'), so the reference resolver finds them in the known-
    skills set and must NOT emit the info log for them.
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "superpowers@mkt", skills=["writing-plans"])

    # An owned agent that references the plugin skill in applicable_skills.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "general-purpose.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: general-purpose
            description: The router agent.
            triggers:
              keywords:
                - {term: "route", weight: 1.0}
            applicable_skills:
              - "superpowers:writing-plans"
            ---
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents_dir,
        plugin_overrides_dir=tmp_path / "no-triggers",
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    # The 'kept as external reference' message must NOT appear for a plugin skill
    # that now has a real catalog entry.
    assert "kept as external reference" not in log_text, (
        "info 'kept as external reference' fired for a plugin skill that "
        "now has a catalog entry — reference resolver should find it by name"
    )


# ---------------------------------------------------------------------------
# Finding #3 regression: description: null must produce "" not "None"
# ---------------------------------------------------------------------------


def test_plugin_file_null_description_produces_empty_string(
    tmp_path: Path,
) -> None:
    """_process_plugin_file must return empty string for null description.

    Regression test for the ``str(None)`` → ``"None"`` bug.  When a plugin
    SKILL.md has ``description: null`` (or omits the key entirely), the
    resulting catalog entry's description field must be an empty string,
    not the literal string ``"None"``.
    """
    from claude_wayfinder.build_catalog import _process_plugin_file

    null_md = tmp_path / "my-skill" / "SKILL.md"
    null_md.parent.mkdir(parents=True)
    # Explicit YAML null value
    null_md.write_text(
        "---\ndescription: null\n---\n",
        encoding="utf-8",
    )
    issues: list = []
    entry = _process_plugin_file(
        null_md,
        kind="skill",
        plugin_name="myplugin@vendor",
        issues_sink=issues,
    )
    assert entry is not None, "entry must be produced even with null description"
    assert (
        entry["description"] != "None"
    ), "description was the literal string 'None' — str(None) bug not fixed"
    assert (
        entry["description"] == ""
    ), f"expected empty string description, got {entry['description']!r}"


# ---------------------------------------------------------------------------
# Finding #2 regression: invalid plugin entry kind emits fatal ValidationIssue
# ---------------------------------------------------------------------------


def test_invalid_kind_emits_fatal_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """build() emits a fatal ValidationIssue for an unrecognised plugin kind.

    Regression test for the suppressed ``# type: ignore[arg-type]``.  When
    ``discover_plugin_entries`` returns a tuple whose kind field is something
    other than ``"skill"`` or ``"agent"``, ``build()`` must append a fatal
    ``ValidationIssue`` and skip that entry rather than passing the bad value
    through to ``_process_plugin_file`` (which expects a
    ``Literal["skill", "agent"]``).
    """
    import claude_wayfinder.build_catalog._main as bdc_main

    # Minimal plugins_dir with a valid installed_plugins.json so that
    # discover_installed_plugins does not fail before we reach our patch.
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text('{"installed": []}', encoding="utf-8")

    # Monkeypatch discover_plugin_entries to return one entry with an
    # invalid kind value that would normally be unreachable at runtime.
    # Patch in _main where build() actually calls it.
    bad_path = tmp_path / "some.md"
    bad_path.write_text("---\ndescription: test\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        bdc_main,
        "discover_plugin_entries",
        lambda _installs: [("unknown-kind", "testplugin@vendor", bad_path)],
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugins_dir=plugins_dir,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    # build() must log a fatal issue regardless of return code.
    log_text = log.read_text(encoding="utf-8")
    assert "Invalid plugin entry kind" in log_text, (
        "Expected 'Invalid plugin entry kind' fatal message in log, "
        f"but log contains:\n{log_text}"
    )


# ---------------------------------------------------------------------------
# Issue #478 — _process_plugin_override extension + Pass-3 collision-merge
# ---------------------------------------------------------------------------


def _make_triggers_dir(
    root: Path,
    plugin: str,
    skill: str,
    *,
    extra_fields: dict | None = None,
) -> Path:
    """Write a minimal sidecar YAML in triggers/<plugin>/<skill>.yml.

    Args:
        root: Parent directory for the triggers tree.
        plugin: Plugin namespace (e.g. ``"superpowers"``).
        skill: Skill stem (e.g. ``"brainstorming"``).
        extra_fields: Additional YAML fields merged into the sidecar.

    Returns:
        Path to the created ``.yml`` file.
    """
    plugin_dir = root / plugin
    plugin_dir.mkdir(parents=True, exist_ok=True)
    sidecar: dict = {
        "triggers": {
            "keywords": [{"term": "brainstorm", "weight": 1.0}],
        },
        "applicable_agents": ["*"],
    }
    if extra_fields:
        sidecar.update(extra_fields)
    yml_file = plugin_dir / f"{skill}.yml"
    import yaml as _yaml

    yml_file.write_text(_yaml.dump(sidecar), encoding="utf-8")
    return yml_file


# --- kind: agent sidecar field ---


def test_plugin_override_kind_agent_produces_agent_entry(tmp_path: Path) -> None:
    """A sidecar with kind: agent produces a catalog entry with kind='agent'.

    The sidecar YAML carries ``kind: agent`` to signal that this override
    describes an agent rather than the default skill kind.  The resulting
    catalog entry must have ``kind='agent'`` and ``source='plugin-override'``.
    """
    from claude_wayfinder.build_catalog import _process_plugin_override

    sidecar = {
        "kind": "agent",
        "description": "An agent override.",
        "triggers": {
            "keywords": [{"term": "route", "weight": 1.0}],
        },
        "applicable_skills": ["*"],
    }
    issues: list = []
    result = _process_plugin_override("myplugin:my-agent", sidecar, issues_sink=issues)
    assert result is not None, "expected an entry, got None"
    assert result["kind"] == "agent", f"expected kind='agent', got {result['kind']!r}"
    assert result["source"] == "plugin-override"


def test_plugin_override_default_kind_skill_preserves_existing_behavior(
    tmp_path: Path,
) -> None:
    """A sidecar without kind: field defaults to kind='skill'.

    Existing behavior: sidecars that omit the ``kind`` field must
    continue to produce skill entries, preserving backward compatibility.
    """
    from claude_wayfinder.build_catalog import _process_plugin_override

    sidecar = {
        "triggers": {
            "keywords": [{"term": "brainstorm", "weight": 1.0}],
        },
        "applicable_agents": ["*"],
    }
    issues: list = []
    result = _process_plugin_override("superpowers:brainstorming", sidecar, issues_sink=issues)
    assert result is not None, "expected an entry, got None"
    assert result["kind"] == "skill", f"expected kind='skill' (default), got {result['kind']!r}"


def test_plugin_override_invalid_kind_produces_fatal_issue(
    tmp_path: Path,
) -> None:
    """A sidecar with an invalid kind value emits a fatal issue and returns None.

    ``kind`` must be one of ``"skill"`` or ``"agent"``.  Any other value
    is a configuration error and the entry must be excluded with a fatal
    ``ValidationIssue``.
    """
    from claude_wayfinder.build_catalog import _process_plugin_override

    sidecar = {
        "kind": "banana",
        "triggers": {
            "keywords": [{"term": "test", "weight": 1.0}],
        },
    }
    issues: list = []
    result = _process_plugin_override("myplugin:my-skill", sidecar, issues_sink=issues)
    assert result is None, "invalid kind must return None"
    severities = [i.severity for i in issues]
    assert "fatal" in severities, f"expected a fatal ValidationIssue, got severities: {severities}"


# --- disabled: true / tombstone sentinel ---


def test_plugin_override_disabled_returns_sentinel(tmp_path: Path) -> None:
    """A sidecar with disabled: true returns the tombstone sentinel tuple.

    When a sidecar carries ``disabled: true``, ``_process_plugin_override``
    must return ``("disable", entry_name, reason)`` instead of a dict.
    """
    from claude_wayfinder.build_catalog import _process_plugin_override

    sidecar = {
        "disabled": True,
        "reason": "permanently broken — see bug #52226",
    }
    issues: list = []
    result = _process_plugin_override("commit-commands:clean_gone", sidecar, issues_sink=issues)
    assert isinstance(
        result, tuple
    ), f"expected sentinel tuple, got {type(result).__name__}: {result!r}"
    assert result[0] == "disable"
    assert result[1] == "commit-commands:clean_gone"
    assert "permanently broken" in result[2]


# --- applicable_agents_intentional field threading ---


def test_plugin_override_applicable_agents_intentional_threaded(
    tmp_path: Path,
) -> None:
    """applicable_agents_intentional in sidecar is threaded into the entry dict.

    A plugin-override sidecar carrying ``applicable_agents_intentional``
    must have the field copied into the returned entry dict so the audit
    rule can suppress the ``empty-applicable-agents`` NIT.
    """
    from claude_wayfinder.build_catalog import _process_plugin_override

    sidecar = {
        "triggers": {
            "keywords": [{"term": "brainstorm", "weight": 1.0}],
        },
        "applicable_agents": [],
        "applicable_agents_intentional": "router-only interactive skill",
    }
    issues: list = []
    result = _process_plugin_override("superpowers:brainstorming", sidecar, issues_sink=issues)
    assert result is not None, "expected an entry dict, got None"
    assert isinstance(result, dict), f"expected dict, got {type(result).__name__}"
    assert result.get("applicable_agents_intentional") == "router-only interactive skill", (
        f"expected applicable_agents_intentional in entry, got: {result}"
    )


def test_skill_file_applicable_agents_intentional_threaded(
    tmp_path: Path,
) -> None:
    """applicable_agents_intentional in triggers.yml is threaded into the entry dict.

    A skill sidecar (triggers.yml) carrying ``applicable_agents_intentional``
    must have the field copied into the returned entry dict so the audit
    rule can suppress the ``empty-applicable-agents`` NIT.
    """
    from claude_wayfinder.build_catalog import _process_skill_file
    from claude_wayfinder.build_catalog._validate import ValidationIssue

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A router-only skill.\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "triggers.yml").write_text(
        "triggers:\n"
        "  keywords:\n"
        "    - {term: route, weight: 1.0}\n"
        "applicable_agents: []\n"
        "applicable_agents_intentional: router-only interactive skill\n",
        encoding="utf-8",
    )
    issues: list[ValidationIssue] = []
    result = _process_skill_file(skill_dir / "SKILL.md", issues_sink=issues)
    assert result is not None, "expected an entry dict, got None"
    assert result.get("applicable_agents_intentional") == "router-only interactive skill", (
        f"expected applicable_agents_intentional in entry, got: {result}"
    )


# --- Pass-3 collision-merge: owned-name protection ---


def test_plugin_override_targeting_owned_name_is_rejected(
    tmp_path: Path,
) -> None:
    """A plugin override targeting an owned entry is rejected with a warning.

    Owned entries (source='owned') are the authoritative source of truth
    and must never be overridden by plugin overrides.  The owned entry
    must be preserved unchanged.
    """

    # Create an owned skill.
    skills = tmp_path / "skills"
    s = skills / "csv-utils"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: csv-utils\ndescription: CSV helpers.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        "triggers:\n  keywords:\n    - {term: csv, weight: 1.0}\n",
        encoding="utf-8",
    )

    # Create a plugin override targeting the same name.  Override names
    # are always "<plugin>:<skill>", so we need an owned skill whose name
    # follows that convention.  Use "superpowers:brainstorm" for both the
    # owned entry and the plugin-override sidecar so the collision fires.
    owned_skills = tmp_path / "skills2"
    owned_skill_dir = owned_skills / "brainstorm"
    owned_skill_dir.mkdir(parents=True)
    (owned_skill_dir / "SKILL.md").write_text(
        "---\nname: superpowers:brainstorm\ndescription: Owned brainstorm.\n---\n",
        encoding="utf-8",
    )
    (owned_skill_dir / "triggers.yml").write_text(
        "triggers:\n  keywords:\n" "    - {term: brainstorm, weight: 1.0}\n",
        encoding="utf-8",
    )
    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorm.yml").write_text(
        "triggers:\n  keywords:\n" "    - {term: brainstorm, weight: 0.5}\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=owned_skills,
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text, "expected a warning about owned-name protection"
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:brainstorm"),
        None,
    )
    assert entry is not None, "owned entry must remain in catalog"
    # The owned entry's keyword weight must be 1.0 (the override weight 0.5
    # must not have been applied).
    kw = entry["triggers"]["keywords"]
    assert any(k["weight"] == 1.0 for k in kw), f"owned entry was overridden — keywords: {kw}"


def test_plugin_override_collision_merges_replaces_discovered(
    tmp_path: Path,
) -> None:
    """A plugin override replaces a plugin-discovered (dormant) entry in place.

    When a plugin override targets a name that already exists with
    source='plugin', the override replaces the dormant entry and the
    catalog must contain exactly one entry with that name bearing the
    override's triggers.
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "superpowers@mkt", skills=["brainstorming"])

    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        "triggers:\n  keywords:\n"
        "    - {term: brainstorm, weight: 1.0}\n"
        'applicable_agents:\n  - "*"\n',
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    matched = [e for e in catalog["entries"] if e["name"] == "superpowers:brainstorming"]
    assert len(matched) == 1, (
        f"expected exactly one entry named 'superpowers:brainstorming', " f"got {len(matched)}"
    )
    entry = matched[0]
    # Override must have replaced the dormant entry — triggers non-empty.
    kws = entry["triggers"]["keywords"]
    assert len(kws) > 0, "override entry must have triggers (not dormant)"
    log_text = log.read_text(encoding="utf-8")
    assert "override layers on plugin-discovered entry" in log_text, (
        f"expected 'override layers on plugin-discovered entry' in log; " f"log:\n{log_text}"
    )


def test_plugin_override_standalone_appends(tmp_path: Path) -> None:
    """A plugin override with no matching existing entry is appended normally.

    When there is no plugin-discovered entry with the same name, the
    override is simply appended to the catalog (no collision, no warning).
    """

    # No plugins_dir — so no plugin-discovered entries.
    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        "triggers:\n  keywords:\n"
        "    - {term: brainstorm, weight: 1.0}\n"
        'applicable_agents:\n  - "*"\n',
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
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:brainstorming"),
        None,
    )
    assert entry is not None, "standalone override must be present in catalog"
    assert entry["source"] == "plugin-override"


def test_plugin_override_disabled_removes_discovered_entry(tmp_path: Path) -> None:
    """A disabled override tombstones (removes) a plugin-discovered entry.

    When a sidecar carries ``disabled: true`` and the entry name matches
    a plugin-discovered entry, that entry must be removed from the catalog.
    An info log must record the removal with the reason.
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "superpowers@mkt", skills=["brainstorming"])

    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        "disabled: true\nreason: 'tombstone test'\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:brainstorming"),
        None,
    )
    assert entry is None, "tombstoned entry must be absent from catalog"
    log_text = log.read_text(encoding="utf-8")
    assert (
        "plugin entry disabled by override" in log_text
    ), f"expected 'plugin entry disabled by override' in log; log:\n{log_text}"


def test_plugin_override_disabled_targeting_nonexistent_warns(
    tmp_path: Path,
) -> None:
    """A tombstone targeting a nonexistent entry emits a warning.

    When ``disabled: true`` targets an entry name that does not exist
    in the catalog, a warning must be logged.
    """

    # No plugins_dir — nothing to tombstone.
    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        "disabled: true\nreason: 'nothing to remove'\n",
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
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "disable override targets nonexistent entry" in log_text, (
        f"expected 'disable override targets nonexistent entry' warning; " f"log:\n{log_text}"
    )


def test_plugin_override_disabled_targeting_owned_name_rejected(
    tmp_path: Path,
) -> None:
    """A tombstone targeting an owned entry is rejected; owned entry preserved.

    ``disabled: true`` must not be able to remove an owned entry.  The
    owned entry must remain in the catalog and a warning must be logged.
    """

    owned_skills = tmp_path / "skills"
    owned_skill_dir = owned_skills / "brainstorm"
    owned_skill_dir.mkdir(parents=True)
    (owned_skill_dir / "SKILL.md").write_text(
        "---\nname: superpowers:brainstorm\ndescription: Owned.\n---\n",
        encoding="utf-8",
    )
    (owned_skill_dir / "triggers.yml").write_text(
        "triggers:\n  keywords:\n    - {term: brainstorm, weight: 1.0}\n",
        encoding="utf-8",
    )

    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorm.yml").write_text(
        "disabled: true\nreason: 'should not work on owned'\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=owned_skills,
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:brainstorm"),
        None,
    )
    assert entry is not None, "owned entry must not be tombstoned"
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text, "expected a warning for rejected tombstone on owned entry"


def test_disabled_skill_applicable_skills_ref_falls_back_to_external_log(
    tmp_path: Path,
) -> None:
    """After tombstoning a plugin skill, applicable_skills refs log as external.

    When a plugin skill is present in the catalog (source='plugin') and an
    agent references it in ``applicable_skills``, the reference resolves
    normally.  After the skill is tombstoned (disabled override), the entry
    is removed from the catalog and the same reference falls back to the
    'kept as external reference' info log — because the entry is gone.
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "superpowers@mkt", skills=["brainstorming"])

    # An owned agent that references the plugin skill in applicable_skills.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "general-purpose.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: general-purpose
            description: The router agent.
            triggers:
              keywords:
                - {term: "route", weight: 1.0}
            applicable_skills:
              - "superpowers:brainstorming"
            ---
            """
        ),
        encoding="utf-8",
    )

    # Tombstone the plugin skill.
    triggers_root = tmp_path / "triggers"
    sp_dir = triggers_root / "superpowers"
    sp_dir.mkdir(parents=True)
    (sp_dir / "brainstorming.yml").write_text(
        "disabled: true\nreason: 'removed for testing'\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents_dir,
        plugin_overrides_dir=triggers_root,
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-09T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    # After tombstoning, the skill is gone — the reference must now be
    # logged as an external (unverified) plugin reference.
    assert "kept as external reference" in log_text, (
        "expected 'kept as external reference' info after tombstoning the "
        f"plugin skill; log:\n{log_text}"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Issue #140 — Pass 3b: plugin-agent sidecar overrides
# ---------------------------------------------------------------------------


def test_discover_plugin_agent_overrides_finds_namespaced_agent_entries(
    tmp_path: Path,
) -> None:
    """walk triggers/<plugin>/agents/<name>.yml yields (agent, <p>:<n>, dict).

    The function must return tuples whose first element is ``"agent"`` and
    whose second element is the plugin-namespaced entry name.  This is the
    primary contract difference from ``discover_plugin_overrides``, which
    returns ``"skill"`` tuples.
    """
    from claude_wayfinder.build_catalog import discover_plugin_agent_overrides

    triggers_root = tmp_path / "triggers"
    agents_dir = triggers_root / "superpowers" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "doc-writer.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "document", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )
    ms_agents_dir = triggers_root / "microsoft-docs" / "agents"
    ms_agents_dir.mkdir(parents=True)
    (ms_agents_dir / "reviewer.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "review", weight: 1.0 }
            applicable_skills: ["python"]
            """
        ),
        encoding="utf-8",
    )
    entries = discover_plugin_agent_overrides(triggers_root)
    kinds = {kind for kind, _name, _sidecar in entries}
    names = {name for _kind, name, _sidecar in entries}
    assert kinds == {"agent"}, f"expected all tuples to have kind='agent', got {kinds}"
    assert "superpowers:doc-writer" in names
    assert "microsoft-docs:reviewer" in names


def test_discover_plugin_agent_overrides_skips_builtin_subdir(
    tmp_path: Path,
) -> None:
    """triggers/builtin/agents/ must not be walked by the agent-override walker.

    The ``builtin/`` subtree is reserved for Pass 2.6.  The agent-override
    walker must skip the entire ``builtin/`` directory, including any
    ``agents/`` subdirectory it might contain.
    """
    from claude_wayfinder.build_catalog import discover_plugin_agent_overrides

    triggers_root = tmp_path / "triggers"
    # Create a builtin/agents/ sidecar — should be ignored.
    builtin_agents_dir = triggers_root / "builtin" / "agents"
    builtin_agents_dir.mkdir(parents=True)
    (builtin_agents_dir / "Explore.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "explore", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )
    # Create a real plugin agent override — should be included.
    real_agents_dir = triggers_root / "superpowers" / "agents"
    real_agents_dir.mkdir(parents=True)
    (real_agents_dir / "brainstormer.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "brainstorm", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )
    entries = discover_plugin_agent_overrides(triggers_root)
    names = {name for _kind, name, _sidecar in entries}
    assert "builtin:Explore" not in names, (
        "builtin/agents/ sidecar must be skipped by discover_plugin_agent_overrides"
    )
    assert "superpowers:brainstormer" in names, (
        "real plugin agent override should be found"
    )


def test_discover_plugin_agent_overrides_returns_empty_when_no_agents_dirs(
    tmp_path: Path,
) -> None:
    """Walker returns [] when no triggers/<plugin>/agents/ dirs exist.

    A triggers/ tree that has only flat ``<skill>.yml`` files (no agents/
    subdirs) must produce an empty result — the walker must not confuse
    skill sidecars with agent sidecars.
    """
    from claude_wayfinder.build_catalog import discover_plugin_agent_overrides

    triggers_root = tmp_path / "triggers"
    skill_dir = triggers_root / "superpowers"
    skill_dir.mkdir(parents=True)
    # Flat skill sidecar — must not be picked up.
    (skill_dir / "brainstorming.yml").write_text(
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
    entries = discover_plugin_agent_overrides(triggers_root)
    assert entries == [], f"expected [], got {entries}"


def test_plugin_agent_sidecar_replaces_dormant_plugin_agent(
    tmp_path: Path,
) -> None:
    """Pass 3b: matched sidecar produces routable plugin-override agent entry.

    A dormant plugin agent (source='plugin') targeted by a
    ``triggers/<plugin>/agents/<name>.yml`` sidecar must:
      - become source='plugin-override'
      - become kind='agent'
      - have routable=True explicitly set
      - carry the sidecar's triggers
      - carry the sidecar's applicable_skills
    """

    plugin_root = tmp_path / "plugins"
    _make_plugin_install(plugin_root, "superpowers@mkt", agents=["doc-writer"])

    triggers_root = tmp_path / "triggers"
    agents_dir = triggers_root / "superpowers" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "doc-writer.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "document", weight: 1.0 }
            applicable_skills: ["*"]
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
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "superpowers:doc-writer"),
        None,
    )
    assert entry is not None, "superpowers:doc-writer missing from catalog"
    assert entry["source"] == "plugin-override", (
        f"expected source='plugin-override', got {entry['source']!r}"
    )
    assert entry["kind"] == "agent", f"expected kind='agent', got {entry['kind']!r}"
    assert entry.get("routable") is True, (
        f"expected routable=True, got {entry.get('routable')!r}"
    )
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "document" in kw_terms, (
        f"sidecar keyword 'document' not in catalog triggers {kw_terms}"
    )
    assert entry.get("applicable_skills") == ["*"], (
        f"expected applicable_skills=['*'], got {entry.get('applicable_skills')!r}"
    )


def test_plugin_agent_sidecar_unmatched_emits_warning_and_is_dropped(
    tmp_path: Path,
) -> None:
    """Pass 3b: unmatched sidecar (ghost) emits a warning and is not added.

    An agent sidecar that targets a name with no matching dormant plugin
    entry must not append a new entry to the catalog, and must emit a
    warning-level log entry identifying the ghost sidecar.
    """

    # No plugin installed — no dormant 'superpowers:ghost-agent' entry.
    triggers_root = tmp_path / "triggers"
    agents_dir = triggers_root / "superpowers" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "ghost-agent.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "ghost", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        plugin_overrides_dir=triggers_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = [e["name"] for e in catalog["entries"]]
    assert "superpowers:ghost-agent" not in names, (
        "ghost agent sidecar must not produce a catalog entry"
    )
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning" in log_text, "ghost sidecar must emit a warning"
    assert "ghost-agent" in log_text, (
        "warning must identify the ghost sidecar target name"
    )


def test_plugin_agent_sidecar_namespace_collision_skill_and_agent_coexist(
    tmp_path: Path,
) -> None:
    """Q3: (kind='skill', name='p:n') and (kind='agent', name='p:n') may coexist.

    When a plugin ships both a skill named ``foo`` and an agent named ``foo``,
    and the user authors both a skill sidecar at ``triggers/p/foo.yml`` and
    an agent sidecar at ``triggers/p/agents/foo.yml``, both entries must be
    present in the catalog because the dedup key is ``(kind, name)``.
    """

    plugin_root = tmp_path / "plugins"
    # Plugin ships both a skill 'foo' and an agent 'foo'.
    install_dir = plugin_root / "myplugin_mkt"
    skill_dir = install_dir / "skills" / "foo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: myplugin:foo\ndescription: Plugin skill foo.\n---\n",
        encoding="utf-8",
    )
    agent_dir = install_dir / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "foo.md").write_text(
        "---\nname: myplugin:foo\ndescription: Plugin agent foo.\n---\n",
        encoding="utf-8",
    )
    _write_manifest(
        plugin_root,
        version=2,
        plugins={
            "myplugin@mkt": [
                {"scope": "user", "installPath": str(install_dir), "version": "1.0"}
            ]
        },
    )

    triggers_root = tmp_path / "triggers"
    # Skill override for 'foo'.
    skill_override_dir = triggers_root / "myplugin"
    skill_override_dir.mkdir(parents=True)
    (skill_override_dir / "foo.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "foo-skill", weight: 1.0 }
            applicable_agents: ["*"]
            """
        ),
        encoding="utf-8",
    )
    # Agent override for 'foo'.
    agent_override_dir = triggers_root / "myplugin" / "agents"
    agent_override_dir.mkdir(parents=True)
    (agent_override_dir / "foo.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "foo-agent", weight: 1.0 }
            applicable_skills: ["*"]
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
        plugins_dir=plugin_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    foo_entries = [e for e in catalog["entries"] if e["name"] == "myplugin:foo"]
    kinds = {e["kind"] for e in foo_entries}
    assert "skill" in kinds, f"skill entry for myplugin:foo not found; entries: {foo_entries}"
    assert "agent" in kinds, f"agent entry for myplugin:foo not found; entries: {foo_entries}"


def test_plugin_agent_sidecar_owned_agent_is_protected(
    tmp_path: Path,
) -> None:
    """Pass 3b: an agent sidecar targeting an owned entry is rejected.

    If the user has an owned agent (source='owned') with the same
    plugin-namespaced name, the sidecar must not overwrite it — consistent
    with the owned-entry protection already applied to skill overrides.
    """

    # Owned agent named 'superpowers:code-writer'.
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "code-writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: superpowers:code-writer
            description: Owned agent that happens to have a plugin namespace.
            triggers:
              keywords:
                - { term: "owned", weight: 1.0 }
            applicable_skills: ["python"]
            ---
            """
        ),
        encoding="utf-8",
    )

    triggers_root = tmp_path / "triggers"
    agents_dir = triggers_root / "superpowers" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "code-writer.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "override-attempt", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        plugin_overrides_dir=triggers_root,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "superpowers:code-writer")
    # The owned entry must be preserved.
    assert entry["source"] == "owned", (
        f"owned entry was overwritten; source is {entry['source']!r}"
    )
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "owned" in kw_terms, "owned entry triggers were replaced by sidecar"
    assert "override-attempt" not in kw_terms, (
        "sidecar triggers must not appear in protected owned entry"
    )
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning" in log_text, "owned-entry protection must emit a warning"


# ---------------------------------------------------------------------------
# Issue #148 — Pass 2b/4b: colocated owned/project agent sidecar overrides
# ---------------------------------------------------------------------------


def test_colocated_sidecar_overrides_inline_triggers_for_owned_agent(
    tmp_path: Path,
) -> None:
    """Pass 2b: matched colocated sidecar replaces inline triggers in owned agent.

    An owned agent at ``agents/code-writer.md`` that has inline ``triggers:``
    frontmatter must have those triggers replaced when a colocated sidecar
    ``agents/code-writer.triggers.yml`` is present.  The resulting catalog
    entry must carry the sidecar triggers and applicable_skills.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "code-writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: code-writer
            description: Writes code.
            triggers:
              keywords:
                - { term: "inline", weight: 1.0 }
            applicable_skills: ["python"]
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "code-writer.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "sidecar", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "code-writer"),
        None,
    )
    assert entry is not None, "code-writer must be present in catalog"
    assert entry["source"] == "owned", (
        f"source must remain 'owned', got {entry['source']!r}"
    )
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "sidecar" in kw_terms, (
        f"sidecar keyword must replace inline triggers; got {kw_terms}"
    )
    assert "inline" not in kw_terms, (
        "inline triggers must be replaced by sidecar"
    )
    assert entry.get("applicable_skills") == ["*"], (
        f"applicable_skills must come from sidecar; got {entry.get('applicable_skills')!r}"
    )
    # D2: warn when sidecar shadows inline triggers
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning" in log_text, (
        "shadowing inline triggers with sidecar must emit a warning"
    )
    assert "code-writer" in log_text, (
        "warning must identify the affected agent"
    )


def test_colocated_sidecar_no_warning_when_no_inline_triggers_for_owned(
    tmp_path: Path,
) -> None:
    """Pass 2b: sidecar on agent with no inline triggers applies cleanly.

    When the owned agent has no inline ``triggers:`` block, the colocated
    sidecar must apply silently — no warning emitted for D2.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "doc-writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: doc-writer
            description: Documents things.
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "doc-writer.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "document", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "doc-writer"),
        None,
    )
    assert entry is not None, "doc-writer must be in catalog"
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "document" in kw_terms, "sidecar triggers must be applied"
    # No shadowing warning when no inline triggers.
    log_text = log_path.read_text(encoding="utf-8")
    assert "shadow" not in log_text, (
        "no shadowing warning when agent had no inline triggers"
    )


def test_colocated_sidecar_orphan_emits_warning_no_entry_for_owned(
    tmp_path: Path,
) -> None:
    """Pass 2b: orphan sidecar (no matching .md) emits warning and is dropped.

    A colocated sidecar ``agents/ghost.triggers.yml`` with no corresponding
    ``agents/ghost.md`` must not produce a catalog entry, and must emit a
    warning identifying the orphan.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    # Orphan sidecar — no ghost.md exists.
    (agents / "ghost.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "ghost", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = [e["name"] for e in catalog["entries"]]
    assert "ghost" not in names, (
        "orphan sidecar must not produce a catalog entry"
    )
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning" in log_text, "orphan sidecar must emit a warning"
    assert "ghost" in log_text, "warning must identify the orphan sidecar stem"


def test_colocated_sidecar_invalid_yaml_warn_skip_owned(
    tmp_path: Path,
) -> None:
    """Pass 2b: sidecar with invalid YAML emits warning and is skipped.

    The agent entry must be preserved with its inline triggers (or empty
    triggers if none).  The build must not crash.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "code-writer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: code-writer
            description: Writes code.
            triggers:
              keywords:
                - { term: "inline-preserved", weight: 1.0 }
            applicable_skills: ["python"]
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "code-writer.triggers.yml").write_text(
        "triggers:\n  keywords:\n    - { term: [unclosed\n",
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "code-writer"),
        None,
    )
    assert entry is not None, "agent entry must be preserved when sidecar has invalid YAML"
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "inline-preserved" in kw_terms, (
        "inline triggers must survive invalid sidecar"
    )
    log_text = log_path.read_text(encoding="utf-8")
    # The sidecar YAML parse failure must produce a warning that identifies
    # the sidecar by name (not just any pre-existing warning like unknown refs).
    assert "code-writer.triggers.yml" in log_text or "code-writer" in log_text.lower(), (
        "invalid sidecar YAML must emit a warning identifying the sidecar"
    )
    # Confirm it was a YAML-parse warning by checking for 'YAML' or 'parse'.
    lower_log = log_text.lower()
    assert "yaml" in lower_log or "parse" in lower_log, (
        "invalid sidecar YAML warning must mention YAML or parse error"
    )


def test_colocated_sidecar_does_not_register_as_agent_entry(
    tmp_path: Path,
) -> None:
    """Q3: *.triggers.yml must not be picked up by the *.md agent glob.

    Placing both ``agents/my-agent.md`` and ``agents/my-agent.triggers.yml``
    in the same directory must result in exactly one catalog entry named
    ``my-agent`` — the ``.triggers.yml`` file must not be processed as an
    agent definition.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "my-agent.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: my-agent
            description: My agent.
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "my-agent.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "my", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    my_agent_entries = [e for e in catalog["entries"] if "my-agent" in e["name"]]
    assert len(my_agent_entries) == 1, (
        f"exactly one entry expected for my-agent; got {my_agent_entries}"
    )
    assert my_agent_entries[0]["kind"] == "agent"


def test_colocated_sidecar_overrides_inline_triggers_for_project_agent(
    tmp_path: Path,
) -> None:
    """Pass 4b: matched colocated sidecar overrides triggers for project agent.

    A project-local agent at ``<repo>/.claude/agents/linter.md`` with inline
    triggers must have those triggers replaced by a colocated
    ``<repo>/.claude/agents/linter.triggers.yml`` sidecar.  Source must
    remain ``"project"``.
    """

    repo = tmp_path / "repo"
    proj_agents = repo / ".claude" / "agents"
    proj_agents.mkdir(parents=True)
    (proj_agents / "linter.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: linter
            description: Runs linters.
            triggers:
              keywords:
                - { term: "inline-proj", weight: 1.0 }
            applicable_skills: []
            ---
            """
        ),
        encoding="utf-8",
    )
    (proj_agents / "linter.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "sidecar-proj", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        project_root=repo,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "linter"),
        None,
    )
    assert entry is not None, "linter must be in catalog"
    assert entry["source"] == "project", (
        f"source must remain 'project', got {entry['source']!r}"
    )
    kw_terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "sidecar-proj" in kw_terms, "sidecar triggers must be applied to project entry"
    assert "inline-proj" not in kw_terms, "inline triggers must be replaced"
    assert entry.get("applicable_skills") == ["*"], (
        f"applicable_skills must come from sidecar; got {entry.get('applicable_skills')!r}"
    )


def test_colocated_sidecar_orphan_dropped_for_project_agent(
    tmp_path: Path,
) -> None:
    """Pass 4b: orphan sidecar in project agents dir emits warning and is dropped.

    A project-local sidecar with no matching ``.md`` counterpart must not
    create a new entry.
    """

    repo = tmp_path / "repo"
    proj_agents = repo / ".claude" / "agents"
    proj_agents.mkdir(parents=True)
    # Orphan sidecar — no ghost.md in project.
    (proj_agents / "ghost-proj.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "proj-ghost", weight: 1.0 }
            applicable_skills: ["*"]
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        project_root=repo,
        now="2026-05-18T00:00:00Z",
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    names = [e["name"] for e in catalog["entries"]]
    assert "ghost-proj" not in names, (
        "orphan project sidecar must not produce a catalog entry"
    )
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning" in log_text, "orphan project sidecar must emit a warning"
    assert "ghost-proj" in log_text, "warning must identify the orphan stem"


# Issue #151 + #153 — _apply_colocated_sidecars validation and routable=True
# ---------------------------------------------------------------------------


def test_colocated_sidecar_keyword_weight_clamped(
    tmp_path: Path,
) -> None:
    """#151: colocated sidecar keywords with off-ladder weights are clamped.

    A sidecar with a keyword weight of 0.7 (not in {0.25, 0.5, 1.0}) must
    be clamped to 0.5 and a warning recorded in all_issues / the log file.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "clamper.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: clamper
            description: Tests weight clamping.
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "clamper.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "clamp-me", weight: 0.7 }
            applicable_skills: []
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "clamper"),
        None,
    )
    assert entry is not None, "clamper must be present in catalog"
    keywords = entry["triggers"]["keywords"]
    assert len(keywords) == 1, f"expected 1 keyword; got {keywords!r}"
    weight = keywords[0]["weight"]
    assert weight in (0.25, 0.5, 1.0), (
        f"weight must be clamped to an allowed value; got {weight}"
    )
    assert weight == 0.5, f"0.7 must clamp to 0.5 (nearest); got {weight}"

    log_text = log_path.read_text(encoding="utf-8")
    assert "clamped" in log_text, (
        "a clamping warning must be written to the log"
    )


def test_colocated_sidecar_keyword_duplicates_deduped(
    tmp_path: Path,
) -> None:
    """#151: colocated sidecar keywords with duplicate terms are deduped.

    A sidecar listing ['foo', 'foo', 'bar'] must produce a catalog entry
    with only ['bar', 'foo'] (deduped, last-wins) and a dedup warning in
    the log.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "deduper.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: deduper
            description: Tests keyword deduplication.
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "deduper.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "foo", weight: 1.0 }
                - { term: "foo", weight: 0.5 }
                - { term: "bar", weight: 1.0 }
            applicable_skills: []
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "deduper"),
        None,
    )
    assert entry is not None, "deduper must be present in catalog"
    terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert terms.count("foo") == 1, (
        f"duplicate 'foo' must be deduplicated; terms={terms!r}"
    )
    assert "bar" in terms, f"'bar' must remain after dedup; terms={terms!r}"

    log_text = log_path.read_text(encoding="utf-8")
    assert "deduplicated" in log_text, (
        "a dedup warning must be written to the log"
    )


def test_colocated_sidecar_deprecated_field_stripped(
    tmp_path: Path,
) -> None:
    """#151: colocated sidecar file_extensions field is stripped with warning.

    A sidecar carrying the deprecated ``file_extensions:`` trigger field must
    have that field stripped from the catalog entry, and a deprecation warning
    must appear in the log.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "stripper.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: stripper
            description: Tests deprecated field stripping.
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "stripper.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "strip", weight: 1.0 }
              file_extensions:
                - ".py"
            applicable_skills: []
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "stripper"),
        None,
    )
    assert entry is not None, "stripper must be present in catalog"
    triggers = entry["triggers"]
    assert "file_extensions" not in triggers, (
        f"deprecated field must be stripped; triggers={triggers!r}"
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "deprecated" in log_text, (
        "a deprecation warning must be written to the log"
    )


def test_colocated_sidecar_sets_routable_true_for_owned_agent(
    tmp_path: Path,
) -> None:
    """#153: colocated sidecar forces routable=True even if frontmatter has False.

    An owned agent with ``routable: false`` in frontmatter must end up with
    ``routable: True`` in the catalog entry once a colocated sidecar is applied.
    """

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "inert.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: inert
            description: Agent that starts non-routable.
            routable: false
            ---
            """
        ),
        encoding="utf-8",
    )
    (agents / "inert.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "activate", weight: 1.0 }
            applicable_skills: []
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=agents,
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "inert"),
        None,
    )
    assert entry is not None, "inert must be present in catalog"
    assert entry.get("routable") is True, (
        f"routable must be True after sidecar applied; got {entry.get('routable')!r}"
    )


def test_colocated_sidecar_sets_routable_true_for_project_agent(
    tmp_path: Path,
) -> None:
    """#153 Pass 4b: colocated sidecar forces routable=True for project agents.

    A project-local agent with ``routable: false`` must end up with
    ``routable: True`` in the catalog entry after a colocated sidecar applies.
    """

    repo = tmp_path / "repo"
    proj_agents = repo / ".claude" / "agents"
    proj_agents.mkdir(parents=True)
    (proj_agents / "dormant-proj.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: dormant-proj
            description: Project agent starting non-routable.
            routable: false
            ---
            """
        ),
        encoding="utf-8",
    )
    (proj_agents / "dormant-proj.triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - { term: "wake-proj", weight: 1.0 }
            applicable_skills: []
            """
        ),
        encoding="utf-8",
    )

    out = tmp_path / "cat.json"
    log_path = tmp_path / "log"
    rc = build(
        skills_dir=tmp_path / "no-skills",
        agents_dir=tmp_path / "no-agents",
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log_path,
        project_root=repo,
        now="2026-05-18T00:00:00Z",
    )
    assert rc == 0
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(
        (e for e in catalog["entries"] if e["name"] == "dormant-proj"),
        None,
    )
    assert entry is not None, "dormant-proj must be present in catalog"
    assert entry.get("routable") is True, (
        "routable must be True after project sidecar applied; "
        f"got {entry.get('routable')!r}"
    )
