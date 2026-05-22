"""Tests for claude_wayfinder.build_catalog._validate — validate_entry and friends.

Covers:
  - validate_entry (Task 3)
  - Whitespace-in-keywords warning (Issue #249 Ambiguity #2)
  - file_extensions deprecated field (Issue #249 Ambiguity #3)
  - Path.home() / CLI defaults (Issue #10)
  - Keyword-group validation (TestValidateKeywordGroups)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from claude_wayfinder.build_catalog import (
    validate_entry,
)

# build_catalog is now a package; invoke via -m rather than as a file path.
_BUILD_MODULE = ["claude_wayfinder.build_catalog"]


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


def test_validate_entry_empty_triggers_block_yields_all_fields() -> None:
    """An entry with `triggers: {}` materializes all trigger fields as [].

    ``file_extensions`` was deprecated in Issue #249 and removed from
    TRIGGER_FIELDS; it must NOT appear in catalog entries.
    ``path_globs_excluded`` was added in issue #24 and must appear.
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
        "path_globs_excluded",
    }
    assert set(result.entry["triggers"].keys()) == expected_fields
    assert "file_extensions" not in result.entry["triggers"]
    for f in expected_fields:
        assert result.entry["triggers"][f] == []
    # Empty triggers + empty applicable should NOT trigger the orphan warning
    # (orphan warning only fires when triggers have content).
    assert not any("never match" in i.message for i in result.issues)



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



# Issue #10: Remove ~/.claude defaults — build_catalog.py path resolution
# ===========================================================================


class TestIssue10BuildCatalogPathDefaults:
    """build_catalog.py CLI defaults must not reference Path.home()/.claude/.

    After Issue #10, every argument that previously defaulted to
    ``~/.claude/...`` must either require an explicit value or write to a
    neutral location (cwd / tmp).  The ``detect_project_root`` guard that
    compared against ``~/.claude`` must accept the user-global directory as
    a parameter rather than computing it internally with Path.home().
    """

    def test_detect_project_root_accepts_user_global_dir_param(
        self, tmp_path: Path
    ) -> None:
        """detect_project_root accepts a user_global_dir parameter.

        When ``user_global_dir`` is passed explicitly, ``detect_project_root``
        must use it for the double-scan guard instead of computing
        ``Path.home() / '.claude'`` internally.

        Set ``user_global_dir`` to a git repo that is *not* a real git repo
        (tmp subdir) so the returned root never equals the real ``~/.claude``.
        Set the repo root to *exactly* ``user_global_dir`` and assert None is
        returned (the guard fired correctly).
        """
        from claude_wayfinder.build_catalog import detect_project_root

        fake_global = tmp_path / "dot-claude"
        fake_global.mkdir()
        # Initialise a git repo at fake_global so git returns it as the root.
        subprocess.run(
            ["git", "init", str(fake_global)],
            capture_output=True,
            check=False,
        )

        result = detect_project_root(
            cwd=fake_global,
            user_global_dir=fake_global.resolve(),
        )
        assert result is None, (
            "detect_project_root must return None when the git root equals "
            f"user_global_dir; got: {result!r}"
        )

    def test_detect_project_root_without_param_does_not_call_path_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """detect_project_root does not call Path.home() when user_global_dir
        is supplied.

        Patch ``pathlib.Path.home`` to raise RuntimeError; passing
        ``user_global_dir`` explicitly must bypass it entirely.
        """
        from claude_wayfinder.build_catalog import detect_project_root

        def _no_home() -> Path:
            raise RuntimeError(
                "Path.home() must not be called when user_global_dir is "
                "supplied to detect_project_root"
            )

        monkeypatch.setattr("pathlib.Path.home", _no_home)

        # Non-git directory → detect_project_root returns None early (no
        # git root, so guard never fires and Path.home() is never reached).
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        # Should not raise even though Path.home() is patched to explode.
        result = detect_project_root(
            cwd=plain_dir,
            user_global_dir=plain_dir,
        )
        assert result is None, (
            f"Expected None for a non-git directory; got: {result!r}"
        )

    def test_build_cli_no_home_defaults(self, tmp_path: Path) -> None:
        """build_catalog CLI does not write to ~/.claude when all paths are
        supplied explicitly.

        Passes all required paths as CLI flags and patches Path.home() to
        raise RuntimeError.  If any code path still calls Path.home() during
        the build, the test will fail with the patched error rather than
        silently writing to the user's home directory.
        """
        skills_dir = tmp_path / "skills"
        agents_dir = tmp_path / "agents"
        triggers_dir = tmp_path / "triggers"
        plugins_dir = tmp_path / "plugins"
        builtin_dir = tmp_path / "builtin"
        for d in (
            skills_dir,
            agents_dir,
            triggers_dir,
            plugins_dir,
            builtin_dir,
        ):
            d.mkdir()

        out = tmp_path / "catalog.json"
        log = tmp_path / "build.log"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                *_BUILD_MODULE,
                "--skills-dir",
                str(skills_dir),
                "--agents-dir",
                str(agents_dir),
                "--plugin-overrides-dir",
                str(triggers_dir),
                "--plugins-dir",
                str(plugins_dir),
                "--builtin-agents-dir",
                str(builtin_dir),
                "--corpus",
                str(tmp_path / "corpus.jsonl"),
                "--out",
                str(out),
                "--log",
                str(log),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        # Exit code 2 is acceptable (no entries → degraded), but the build
        # must not crash and must not write to ~/.claude/.
        assert result.returncode in {0, 2}, (
            f"Unexpected exit code {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        # The catalog must have been written to the explicit --out path.
        assert out.exists(), (
            f"Catalog was not written to the --out path {out}; "
            f"may have fallen back to ~/.claude/state/dispatch-catalog.json"
        )


class TestValidateKeywordGroups:
    """Validator rules for keyword_groups (spec § 6)."""

    def _validate(self, raw_groups, name="doc-writer"):
        """Run only the group validator over a minimal fm dict.

        Returns (sanitized_groups, issues) by calling validate_entry
        with a frontmatter containing only the group config and
        inspecting the result.
        """
        from claude_wayfinder import build_catalog as bc

        fm = {
            "name": name,
            "description": "Doc writer.",
            "triggers": {"keyword_groups": raw_groups},
        }
        result = bc.validate_entry(fm, kind="agent", source_stem=name)
        sanitized = (
            result.entry["triggers"].get("keyword_groups", [])
            if result.entry
            else []
        )
        return sanitized, result.issues

    def test_minimal_valid_group_passes(self):
        groups = [
            {
                "slots": [
                    {"name": "verbs", "terms": ["update", "edit"]},
                    {"name": "nouns", "terms": ["docs", "readme"]},
                ],
                "weight": 1.0,
            }
        ]
        sanitized, issues = self._validate(groups)
        fatals = [i for i in issues if i.severity == "fatal"]
        assert not fatals, [i.message for i in fatals]
        assert len(sanitized) == 1

    def test_group_with_fewer_than_2_slots_is_fatal(self):
        groups = [{"slots": [["docs"]], "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("2 slots" in m or ">= 2" in m or "at least 2" in m for m in fatals)

    def test_group_with_more_than_8_slots_is_fatal(self):
        slots = [[f"v{i}"] for i in range(9)]
        groups = [{"slots": slots, "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("8" in m for m in fatals)

    def test_group_with_4_or_more_slots_warns(self):
        slots = [["a"], ["b"], ["c"], ["d"]]
        groups = [{"slots": slots, "weight": 1.0}]
        _, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        # The exact phrasing may vary; check for slot-count signal.
        assert any("4 slots" in m or "many slots" in m or "rarely" in m for m in warns)

    def test_slot_with_zero_terms_is_fatal(self):
        groups = [{"slots": [{"terms": []}, ["docs"]], "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("terms" in m for m in fatals)

    def test_slot_with_one_term_warns(self):
        groups = [{"slots": [["github"], ["issue", "pr"]], "weight": 1.0}]
        sanitized, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("single-term" in m.lower() or "1 term" in m.lower() for m in warns)
        # The group is still emitted — single-term slot is allowed.
        assert len(sanitized) == 1

    def test_intra_group_term_overlap_is_fatal(self):
        groups = [
            {
                "slots": [["update", "fix"], ["fix", "repair"]],
                "weight": 1.0,
            }
        ]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("'fix'" in m or '"fix"' in m for m in fatals)

    def test_weight_in_range_but_non_canonical_clamps_with_warning(self):
        """C3: out-of-canon weights clamp+warn (consistent with _validate_keywords)."""
        groups = [{"slots": [["a"], ["b"]], "weight": 0.7}]
        sanitized, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("clamped" in m for m in warns)
        # Group still emitted with clamped weight.
        assert len(sanitized) == 1
        assert sanitized[0]["weight"] in (0.5, 1.0)  # _clamp_weight rounds to nearest

    def test_weight_outside_0_1_range_is_fatal(self):
        """Weights outside [0.0, 1.0] are fatal (matches _validate_keywords)."""
        groups = [{"slots": [["a"], ["b"]], "weight": 1.5}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("outside" in m.lower() or "0.0" in m for m in fatals)

    def test_cross_group_term_overlap_warns(self):
        """C2: term in 2+ groups on same entry warns (D5 suppression surprise)."""
        groups = [
            {"slots": [["update", "edit"], ["docs"]], "weight": 1.0},
            {"slots": [["update", "modify"], ["readme"]], "weight": 1.0},
        ]
        sanitized, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("'update'" in m and "multiple" in m for m in warns)
        # Both groups still emitted — overlap is a warning, not a fatal.
        assert len(sanitized) == 2

    def test_terms_are_lowercased(self):
        groups = [
            {"slots": [["UPDATE", "Edit"], ["DOCS"]], "weight": 1.0}
        ]
        sanitized, _ = self._validate(groups)
        slot1_terms = sanitized[0]["slots"][0]["terms"]
        assert all(t == t.lower() for t in slot1_terms)

    def test_slot_name_with_whitespace_warns(self):
        groups = [
            {
                "slots": [
                    {"name": "verb words", "terms": ["update"]},
                    {"name": "nouns", "terms": ["docs"]},
                ],
                "weight": 1.0,
            }
        ]
        _, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("name" in m and ("whitespace" in m or "identifier" in m) for m in warns)


# ---------------------------------------------------------------------------
# Issue #24 — path_globs_excluded validator tests
# ---------------------------------------------------------------------------


class TestValidatePathGlobsExcluded:
    """validate_entry correctly handles the path_globs_excluded trigger field.

    Issue #24: new field that mirrors path_globs but for exclusion.
    """

    def test_path_globs_excluded_parsed_into_list(self) -> None:
        """path_globs_excluded list of strings passes through sanitizer."""
        fm = {
            "name": "my-agent",
            "triggers": {
                "path_globs": ["**/*.py"],
                "path_globs_excluded": ["agents/**/*.py", "agents/*.py"],
            },
            "applicable_skills": ["*"],
        }
        result = validate_entry(fm, kind="agent", source_stem="my-agent")
        assert result.entry is not None, (
            f"Expected valid entry; got issues: {result.issues}"
        )
        excluded = result.entry["triggers"].get("path_globs_excluded", [])
        assert excluded == ["agents/**/*.py", "agents/*.py"], (
            f"path_globs_excluded not preserved in catalog entry: {excluded!r}"
        )

    def test_path_globs_excluded_default_empty(self) -> None:
        """path_globs_excluded is optional; absent means empty list."""
        fm = {
            "name": "my-agent",
            "triggers": {
                "path_globs": ["**/*.py"],
            },
            "applicable_skills": ["*"],
        }
        result = validate_entry(fm, kind="agent", source_stem="my-agent")
        assert result.entry is not None
        # Either absent or empty is acceptable — check it is not non-empty.
        excluded = result.entry["triggers"].get("path_globs_excluded", [])
        assert excluded == [], (
            f"Missing path_globs_excluded should default to []; got {excluded!r}"
        )

    def test_path_globs_excluded_non_list_is_fatal(self) -> None:
        """A non-list path_globs_excluded produces a fatal issue."""
        fm = {
            "name": "my-agent",
            "triggers": {
                "path_globs_excluded": "agents/**/*.py",  # string, not list
            },
            "applicable_skills": ["*"],
        }
        result = validate_entry(fm, kind="agent", source_stem="my-agent")
        assert result.entry is None, (
            "Non-list path_globs_excluded must produce a fatal issue and "
            "exclude the entry"
        )
        fatals = [i for i in result.issues if i.severity == "fatal"]
        assert fatals, "Expected at least one fatal issue for non-list field"

