"""Reviewer prompts challenge proposed remediations without restating their schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROMPTS = (
    "bigoh",
    "countercase",
    "north_star",
    "redgreen",
    "smellcheck",
    "taintcheck",
)
ROOT = Path("roundtable/configs/buddies/prompts/Reviewer/Agents")
SCHEMAS = Path("roundtable/configs/buddies/schemas")


def test_redgreen_does_not_invent_test_harness_constructs() -> None:
    prompt = " ".join((ROOT / "redgreen.agent.md").read_text(encoding="utf-8").lower().split())
    assert "test locations, fixtures, helpers, and result shapes you actually located" in prompt
    assert "never invent repository apis" in prompt


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_body_does_not_copy_remediation_field_names(name: str) -> None:
    prompt = (ROOT / f"{name}.agent.md").read_text(encoding="utf-8")
    schema_mechanics = (
        "`intent`",
        "`abstentions`",
        "`execution_relevance`",
        "`evidence` field",
        "`reasoning` field",
        "`scope_extension`",
        "`suggestion`",
        "`draft`",
        "`fix`",
        "schema-supported reason",
        "output contract exactly",
        "benchmarked:",
        "anchorIndex",
        "fallbackReason",
        "filePath",
        "startLine",
        "endLine",
    )
    assert not [phrase for phrase in schema_mechanics if phrase in prompt]


@pytest.mark.parametrize(
    ("name", "report_fields", "finding_fields"),
    (
        ("bigoh", {"intent", "findings", "abstentions"}, {"execution_relevance", "remediation"}),
        ("countercase", {"intent", "findings", "abstentions"}, {"evidence", "remediation"}),
        ("north_star", {"intent", "findings"}, {"remediation"}),
        ("redgreen", {"intent", "findings"}, {"reasoning", "evidence", "remediation"}),
        ("smellcheck", {"intent", "findings"}, {"remediation"}),
        ("taintcheck", {"intent", "findings", "abstentions"}, {"remediation"}),
    ),
)
def test_schema_retains_the_output_contract(
    name: str, report_fields: set[str], finding_fields: set[str]
) -> None:
    schema = yaml.safe_load((SCHEMAS / f"{name}.schema.yaml").read_text(encoding="utf-8"))
    assert report_fields <= schema["properties"].keys()
    assert finding_fields <= schema["properties"]["findings"]["items"]["properties"].keys()


@pytest.mark.parametrize("name", ("bigoh", "smellcheck", "taintcheck"))
def test_ado_guidance_names_only_the_granted_facade(name: str) -> None:
    prompt = (ROOT / f"{name}.agent.md").read_text(encoding="utf-8")
    assert "ado-code-read" in prompt
    assert "ado-contoso" not in prompt
    assert "ado-msazure" not in prompt
    assert "ado-*/" not in prompt


def test_taintcheck_owns_proof_before_downstream_adjudication() -> None:
    prompt = " ".join((ROOT / "taintcheck.agent.md").read_text(encoding="utf-8").split()).lower()
    assert "there is no downstream judge" not in prompt
    assert "downstream adjudication" in prompt
    assert "read-only repository and documentation retrieval" in prompt
    assert "live-target probing" in prompt


def test_smellcheck_emits_only_actionable_quality_defects() -> None:
    prompt = (ROOT / "smellcheck.agent.md").read_text(encoding="utf-8")
    assert "Emit `low` findings" not in prompt
    assert "note as `low`/positive" not in prompt
    assert "do **not** recommend acting on now" not in prompt
    assert "A justified deviation is not a finding" in prompt
    assert "actionable quality defect" in prompt


def test_bigoh_uses_only_existing_benchmark_artifacts() -> None:
    prompt = (ROOT / "bigoh.agent.md").read_text(encoding="utf-8")
    assert "existing benchmark or test target" in prompt
    assert "scratch benchmark code" in prompt
    assert "agent-authored or proposed remediation code" in prompt
    assert "modify the worktree" in prompt
