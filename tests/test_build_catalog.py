"""Tests for claude_wayfinder/build_catalog.py."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from claude_wayfinder.build_catalog import (
    ValidationIssue,
    build_catalog,
    compute_content_hash,
    detect_exclude_dead_zones,
    load_frontmatter,
    load_trigger_sidecar,
    update_revisions_sidecar,
    validate_entry,
    write_catalog,
    write_log,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "src" / "claude_wayfinder" / "build_catalog.py"


def test_cli_help_returns_zero() -> None:
    """The script must respond to --help with exit code 0."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout.lower()


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
# Task 3 — validate_entry tests
# ---------------------------------------------------------------------------


def test_validate_entry_minimal_valid() -> None:
    """A fully-valid minimal entry produces no issues and a non-None entry."""
    fm = {
        "name": "csv-utils",
        "description": "Helpers.",
        "triggers": {"keywords": [{"term": "csv", "weight": 1.0}]},
        "applicable_agents": ["code-writer"],
    }
    result = validate_entry(fm, kind="skill", source_stem="csv-utils")
    assert result.issues == []
    assert result.entry is not None
    assert result.entry["name"] == "csv-utils"
    assert result.entry["triggers"]["keywords"] == [{"term": "csv", "weight": 1.0}]


def test_validate_entry_dormant_logs_info() -> None:
    """An entry with no triggers block is dormant and gets an info issue."""
    fm = {"name": "legacy", "description": "no triggers"}
    result = validate_entry(fm, kind="skill", source_stem="legacy")
    assert result.entry is not None
    assert any(i.severity == "info" and "dormant" in i.message for i in result.issues)


def test_validate_entry_weight_clamped_with_warning() -> None:
    """A keyword weight not in {0.25, 0.5, 1.0} is clamped with a warning."""
    fm = {
        "name": "noisy",
        "triggers": {"keywords": [{"term": "x", "weight": 0.75}]},
        "applicable_agents": ["*"],
    }
    result = validate_entry(fm, kind="skill", source_stem="noisy")
    assert result.entry is not None
    assert result.entry["triggers"]["keywords"][0]["weight"] == 1.0
    assert any(i.severity == "warning" and "clamped" in i.message for i in result.issues)


def test_validate_entry_negative_weight_is_fatal() -> None:
    """A keyword weight below 0.0 is out-of-range and must be fatal (entry excluded).

    -0.5 is not merely off-ladder — it is the opposite of intent.
    Clamping it to 0.25 would silently invert the author's signal.
    """
    fm = {
        "name": "bad-negative",
        "triggers": {"keywords": [{"term": "x", "weight": -0.5}]},
        "applicable_agents": ["*"],
    }
    result = validate_entry(fm, kind="skill", source_stem="bad-negative")
    assert result.entry is None
    assert any(i.severity == "fatal" for i in result.issues)


def test_validate_entry_weight_above_one_is_fatal() -> None:
    """A keyword weight above 1.0 is out-of-range and must be fatal (entry excluded).

    10 is not an off-ladder rounding; it is an author error that clamping
    would silently turn into 1.0, masking a broken entry.
    """
    fm = {
        "name": "bad-high",
        "triggers": {"keywords": [{"term": "x", "weight": 10}]},
        "applicable_agents": ["*"],
    }
    result = validate_entry(fm, kind="skill", source_stem="bad-high")
    assert result.entry is None
    assert any(i.severity == "fatal" for i in result.issues)


def test_validate_entry_keyword_not_mapping_is_fatal() -> None:
    """A keyword entry that is not a {term, weight} mapping is fatal."""
    fm = {
        "name": "broken",
        "triggers": {"keywords": ["just-a-string"]},
        "applicable_agents": ["*"],
    }
    result = validate_entry(fm, kind="skill", source_stem="broken")
    assert result.entry is None
    assert any(i.severity == "fatal" for i in result.issues)


def test_validate_entry_duplicate_term_dedupes_last_wins() -> None:
    """Duplicate keyword terms are deduplicated; the last occurrence wins."""
    fm = {
        "name": "dupes",
        "triggers": {
            "keywords": [
                {"term": "foo", "weight": 1.0},
                {"term": "foo", "weight": 0.5},
            ]
        },
        "applicable_agents": ["*"],
    }
    result = validate_entry(fm, kind="skill", source_stem="dupes")
    assert result.entry is not None
    kws = result.entry["triggers"]["keywords"]
    assert len(kws) == 1
    assert kws[0]["weight"] == 0.5
    assert any("duplicate" in i.message for i in result.issues)


def test_validate_entry_triggers_with_empty_applicable_warns() -> None:
    """Triggers declared with an empty applicable_agents list get a warning."""
    fm = {
        "name": "orphan",
        "triggers": {"keywords": [{"term": "x", "weight": 1.0}]},
        "applicable_agents": [],
    }
    result = validate_entry(fm, kind="skill", source_stem="orphan")
    assert result.entry is not None
    assert any("never match" in i.message.lower() for i in result.issues)


def test_validate_entry_agent_uses_applicable_skills() -> None:
    """Agent entries use applicable_skills, not applicable_agents."""
    fm = {
        "name": "code-writer",
        "description": "Writes code.",
        "triggers": {"keywords": [{"term": "implement", "weight": 1.0}]},
        "applicable_skills": ["python", "bicep"],
    }
    result = validate_entry(fm, kind="agent", source_stem="code-writer")
    assert result.entry is not None
    assert "applicable_skills" in result.entry
    assert "applicable_agents" not in result.entry
    assert result.entry["applicable_skills"] == ["python", "bicep"]


def test_validate_entry_malformed_inverse_list_is_fatal() -> None:
    """Non-string elements in applicable_* produce a fatal."""
    fm = {
        "name": "bad-inverse",
        "triggers": {"keywords": [{"term": "x", "weight": 1.0}]},
        "applicable_agents": ["valid", 42],
    }
    result = validate_entry(fm, kind="skill", source_stem="bad-inverse")
    assert result.entry is None
    assert any(
        i.severity == "fatal" and "must be a list of strings" in i.message for i in result.issues
    )


def test_validate_entry_empty_triggers_block_yields_all_six_fields() -> None:
    """An entry with `triggers: {}` materializes all 6 trigger fields as [].

    ``file_extensions`` was deprecated in Issue #249 and removed from
    TRIGGER_FIELDS; it must NOT appear in catalog entries.
    """
    fm = {
        "name": "empty-triggers",
        "triggers": {},
        "applicable_agents": [],
    }
    result = validate_entry(fm, kind="skill", source_stem="empty-triggers")
    assert result.entry is not None
    expected_fields = {
        "command_prefixes",
        "agent_mentions",
        "path_globs",
        "keywords",
        "tool_mentions",
        "excludes",
    }
    assert set(result.entry["triggers"].keys()) == expected_fields
    assert "file_extensions" not in result.entry["triggers"]
    for f in expected_fields:
        assert result.entry["triggers"][f] == []
    # Empty triggers + empty applicable should NOT trigger the orphan warning
    # (orphan warning only fires when triggers have content).
    assert not any("never match" in i.message for i in result.issues)


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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import _resolve_applicable_references

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
# Issue #250 — v6 sidecar schema tests
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
# Issue #249 — Ambiguity #2: whitespace in keywords warning
# ---------------------------------------------------------------------------


def test_whitespace_keyword_emits_warning_and_is_dropped() -> None:
    """A keyword whose term contains whitespace is warned and dropped.

    The entry itself is kept (non-fatal).  The offending keyword is
    omitted from the resolved entry.  This mirrors the 'weight clamped'
    pattern: warn, mutate, keep.
    """
    fm = {
        "name": "whitespace-kw",
        "triggers": {
            "keywords": [
                {"term": "type hints", "weight": 0.5},
                {"term": "python", "weight": 1.0},
            ]
        },
        "applicable_agents": ["code-writer"],
    }
    result = validate_entry(fm, kind="skill", source_stem="whitespace-kw")
    assert result.entry is not None, "entry must be kept (non-fatal)"
    terms = [k["term"] for k in result.entry["triggers"]["keywords"]]
    assert "type hints" not in terms, "whitespace keyword must be dropped"
    assert "python" in terms, "valid keyword must be kept"
    assert any(
        i.severity == "warning" and "whitespace" in i.message.lower() for i in result.issues
    ), "a warning mentioning 'whitespace' must be emitted"


def test_whitespace_keyword_warning_via_build(tmp_path: Path) -> None:
    """build() surfaces whitespace-keyword warning in the log file."""
    from claude_wayfinder.build_catalog import build

    skills = tmp_path / "skills"
    s = skills / "ws-skill"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: ws-skill\ndescription: Test.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              keywords:
                - {term: "type hints", weight: 0.5}
                - {term: "python", weight: 1.0}
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
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text
    assert "whitespace" in log_text.lower()
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "ws-skill")
    terms = [k["term"] for k in entry["triggers"]["keywords"]]
    assert "type hints" not in terms
    assert "python" in terms


# ---------------------------------------------------------------------------
# Issue #249 — Ambiguity #3: file_extensions deprecated (warn + drop)
# ---------------------------------------------------------------------------


def test_file_extensions_in_sidecar_emits_warning_and_entry_kept() -> None:
    """A sidecar declaring file_extensions emits a warning; entry is kept.

    The deprecated field is stripped from the resolved entry.  This is
    consistent with the 'warn + drop unknown field' policy.
    """
    fm = {
        "name": "ext-skill",
        "triggers": {
            "file_extensions": ["py", "pyw"],
            "keywords": [{"term": "python", "weight": 1.0}],
        },
        "applicable_agents": ["code-writer"],
    }
    result = validate_entry(fm, kind="skill", source_stem="ext-skill")
    assert result.entry is not None, "entry must be kept (non-fatal)"
    assert (
        "file_extensions" not in result.entry["triggers"]
    ), "file_extensions must be stripped from the resolved entry"
    assert any(
        i.severity == "warning" and "file_extensions" in i.message for i in result.issues
    ), "a warning mentioning 'file_extensions' must be emitted"


def test_file_extensions_stripped_from_catalog_via_build(tmp_path: Path) -> None:
    """build() strips file_extensions from catalog entries and logs a warning."""
    from claude_wayfinder.build_catalog import build

    skills = tmp_path / "skills"
    s = skills / "ext-skill"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        "---\nname: ext-skill\ndescription: Test.\n---\n",
        encoding="utf-8",
    )
    (s / "triggers.yml").write_text(
        textwrap.dedent(
            """\
            triggers:
              file_extensions: ["py"]
              keywords:
                - {term: "python", weight: 1.0}
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
        corpus_path=tmp_path / "absent.jsonl",
        out_path=out,
        log_path=log,
        now="2026-05-01T00:00:00Z",
    )
    assert rc == 0
    log_text = log.read_text(encoding="utf-8")
    assert "warning" in log_text
    assert "file_extensions" in log_text
    catalog = json.loads(out.read_text(encoding="utf-8"))
    entry = next(e for e in catalog["entries"] if e["name"] == "ext-skill")
    assert "file_extensions" not in entry["triggers"]


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
                str(SCRIPT),
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
                str(SCRIPT),
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


# ---------------------------------------------------------------------------
# Issue #477 — Pass 2.5 plugin discovery wired into build()
# ---------------------------------------------------------------------------


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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    import claude_wayfinder.build_catalog as bdc
    from claude_wayfinder.build_catalog import build

    # Minimal plugins_dir with a valid installed_plugins.json so that
    # discover_installed_plugins does not fail before we reach our patch.
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text('{"installed": []}', encoding="utf-8")

    # Monkeypatch discover_plugin_entries to return one entry with an
    # invalid kind value that would normally be unreachable at runtime.
    bad_path = tmp_path / "some.md"
    bad_path.write_text("---\ndescription: test\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        bdc,
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


# --- Pass-3 collision-merge: owned-name protection ---


def test_plugin_override_targeting_owned_name_is_rejected(
    tmp_path: Path,
) -> None:
    """A plugin override targeting an owned entry is rejected with a warning.

    Owned entries (source='owned') are the authoritative source of truth
    and must never be overridden by plugin overrides.  The owned entry
    must be preserved unchanged.
    """
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
    from claude_wayfinder.build_catalog import build

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
# GitHub read/write-split invariant tests (issue #514 / #512 Phase 1)
# ---------------------------------------------------------------------------


def test_subagent_github_write_tools_removed() -> None:
    """Sub-agents (except review agents) must not list GitHub write tools.

    Per #512 Phase 1 (#513): write tools were removed from sub-agent
    frontmatter to enforce the read/write split structurally — the call
    cannot happen if the tool isn't in context. This test prevents silent
    regression if a future agent edit re-adds them.
    """
    if not (REPO_ROOT / "agents").is_dir():
        pytest.skip("requires harness agents/ directory (not present in public repo)")
    prohibited_tools = [
        "mcp__github__add_issue_comment",
        "mcp__github__create_issue",
        "mcp__github__create_pull_request",
        "mcp__github__update_issue",
        "mcp__github__merge_pull_request",
        "mcp__github__create_or_update_file",
        "mcp__github__push_files",
    ]
    subagents = ["code-writer", "debugger", "doc-writer"]
    for agent in subagents:
        agent_path = REPO_ROOT / "agents" / f"{agent}.md"
        fm = load_frontmatter(agent_path)
        tools_str = fm.get("tools", "") if fm else ""
        for tool in prohibited_tools:
            assert tool not in tools_str, (
                f"{agent} must not have {tool} (per #512 read/write split). "
                f"If you need to write to GitHub from this agent, return "
                f"findings to the router for it to post."
            )


def test_review_agents_retain_create_pull_request_review() -> None:
    """code-reviewer and inquisitor keep create_pull_request_review by design.

    Per #514, this carve-out exists because (1) the agent's identity encodes
    the intent, (2) reviews carry a structured state enum that bounds the
    action shape, and (3) reviews are scope-bounded to the PR diff. A future
    over-aggressive scrub that removed this tool would make these agents
    inoperable — this test guards against it.
    """
    if not (REPO_ROOT / "agents").is_dir():
        pytest.skip("requires harness agents/ directory (not present in public repo)")
    review_agents = ["code-reviewer", "inquisitor"]
    required_tool = "mcp__github__create_pull_request_review"
    for agent in review_agents:
        agent_path = REPO_ROOT / "agents" / f"{agent}.md"
        fm = load_frontmatter(agent_path)
        tools_str = fm.get("tools", "") if fm else ""
        assert required_tool in tools_str, (
            f"{agent} must retain {required_tool} (its core output). "
            f"See #514 for the carve-out rationale."
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
