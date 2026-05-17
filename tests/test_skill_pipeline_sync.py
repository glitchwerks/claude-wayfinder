"""Drift check: ensure skill body's numbered steps match setup_pipeline.py functions.

If the skill body and the executable mirror drift apart, this test fails — forcing
the author to update both together.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_BODY = Path(__file__).parent.parent / "skills" / "setup-wayfinder" / "SKILL.md"
PIPELINE = Path(__file__).parent / "integration" / "setup_pipeline.py"


# Maps the human-readable step heading in the skill body to the function name
# in setup_pipeline.py. Update this map when adding/removing steps.
STEP_FUNCTION_MAP = {
    "Resolve `${CLAUDE_PLUGIN_DATA}`": "compute_plugin_data_dir",
    "Discover Python": "discover_python",
    "Wipe the existing venv": "wipe_venv",
    "Create the venv": "create_venv",
    "Install claude-wayfinder from PyPI": "pip_install",
    "Verify import": "verify_import",
    "Write the setup-state flag": "write_flag",
    # Step 8 (tell user) is intentionally not mirrored — it's a skill-body-only
    # responsibility; the executable mirror's caller handles success reporting.
}


def test_skill_body_lists_expected_step_count() -> None:
    """Skill body must contain exactly 8 sequential step headings."""
    body = SKILL_BODY.read_text(encoding="utf-8")
    # Match "## Step N: ..." headings
    step_headings = re.findall(r"^##\s+Step\s+(\d+):", body, re.MULTILINE)
    assert len(step_headings) == 8, (
        f"Expected 8 steps in skill body, found {len(step_headings)}"
    )
    assert step_headings == ["1", "2", "3", "4", "5", "6", "7", "8"], (
        "Steps should be numbered 1-8 consecutively"
    )


@pytest.mark.parametrize("step_heading,function_name", STEP_FUNCTION_MAP.items())
def test_skill_step_has_matching_function(step_heading: str, function_name: str) -> None:
    """Each skill body step heading corresponds to a function in setup_pipeline.py."""
    body = SKILL_BODY.read_text(encoding="utf-8")
    pipeline = PIPELINE.read_text(encoding="utf-8")
    # Anchored: heading must appear as part of a `## Step N: ...` line
    heading_pattern = rf"^##\s+Step\s+\d+:\s+.*{re.escape(step_heading)}"
    assert re.search(heading_pattern, body, re.MULTILINE), (
        f"Step heading not found as a `## Step N:` heading in skill body: {step_heading}"
    )
    pattern = rf"^def\s+{re.escape(function_name)}\s*\("
    assert re.search(pattern, pipeline, re.MULTILINE), (
        f"Function {function_name}() not found in setup_pipeline.py "
        f"(expected because skill body has '## Step ...: {step_heading}')"
    )


def test_pipeline_has_run_full_pipeline_entrypoint() -> None:
    """The executable mirror exposes a single entry that runs all steps."""
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert re.search(r"^def\s+run_full_pipeline\s*\(", pipeline, re.MULTILINE), (
        "setup_pipeline.py should expose run_full_pipeline() that runs all 8 steps"
    )
