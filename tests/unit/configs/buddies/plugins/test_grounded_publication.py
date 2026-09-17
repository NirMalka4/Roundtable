"""One grounded record per thread: the Judge's citations, and nothing else.

Reviewer anchors position a thread; they never appear as grounding. These lock the
split, because the shipped taintcheck example is exactly the shape that produced the
defect: four anchors, one of them (`38-44`) a span containing another (`42`), and one
of them plumbing rather than evidence.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import roundtable.configs.buddies as buddies_pkg
from roundtable.ado.anchor import AnchorDecision
from roundtable.bundle.paths import set_config_root
from roundtable.configs.buddies.plugins.claims_projector import ClaimsProjector

BUNDLE = Path(buddies_pkg.__file__).parent
_BUDDIES_ROOT = "roundtable/configs/buddies"


def _example(name: str) -> dict[str, Any]:
    schema = yaml.safe_load(
        (BUNDLE / "schemas" / f"{name}.schema.yaml").read_text(encoding="utf-8")
    )
    return copy.deepcopy(schema["examples"][0])


@pytest.fixture(autouse=True)
def _use_buddies_config():
    set_config_root(_BUDDIES_ROOT)
    try:
        yield
    finally:
        set_config_root(None)


def _project(judge: dict[str, Any]):
    decisions = [
        {
            "claim_id": claim["id"],
            "primary_source_finding_id": claim["primary_source_finding_id"],
            "decision": "publish",
            "reason": "The independently read sink contract supports the draft.",
            "evidence": [
                {
                    "source": "repository",
                    "path": "src/data/SearchService.cs",
                    "start_line": 42,
                    "end_line": 42,
                    "excerpt": "command.Parameters.AddWithValue",
                    "observation": "The sink exposes a parameter API.",
                }
            ],
        }
        for claim in judge["claims"]
        if claim.get("disposition") != "rejected"
    ]
    session = {
        "taintcheck": {"response": json.dumps(_example("taintcheck"))},
        "Judge": {"response": json.dumps(judge)},
        "RemedyScout": {"response": json.dumps({"decisions": decisions})},
    }
    return ClaimsProjector().project(session, session_dir_path="/artifacts/repo/session_example")


def _thread(judge: dict[str, Any]) -> str:
    result = _project(judge)
    finding = result.all_findings[0]
    decision = AnchorDecision(
        "inline", finding.file_path, finding.start_line, finding.end_line, None
    )
    return result.commenter.render_thread(finding, decision, session_id="session")


def _grounding_bullets(thread: str) -> list[str]:
    if "**Grounding**" not in thread:
        return []
    section = thread.split("**Grounding**", 1)[1]
    bullets = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:])
        elif stripped.startswith(("**", "### ")):
            break
    return bullets


@pytest.fixture
def judge() -> dict[str, Any]:
    return _example("judge")


def test_grounding_is_exactly_the_judge_citations(judge) -> None:
    bullets = _grounding_bullets(_thread(judge))

    assert len(bullets) == len(judge["claims"][0]["evidence"])
    for citation in judge["claims"][0]["evidence"]:
        assert any(citation["observation"] in bullet for bullet in bullets)


def test_grounding_says_what_each_citation_contributes(judge) -> None:
    """A location and a sentence do not say why the reader is being sent there."""
    bullets = _grounding_bullets(_thread(judge))

    assert any("(the defect)" in bullet for bullet in bullets)
    assert any("(how it is reached)" in bullet for bullet in bullets)


def test_reviewer_anchors_are_not_published_as_grounding(judge) -> None:
    thread = _thread(judge)
    bullets = _grounding_bullets(thread)
    cited = {c["file"] for c in judge["claims"][0]["evidence"]}
    reviewer_only = [
        a["filePath"]
        for a in _example("taintcheck")["findings"][0]["anchors"]
        if a["filePath"] not in cited
    ]

    assert reviewer_only, "fixture must carry an anchor the Judge did not cite"
    assert "reviewer anchor" not in thread
    for path in reviewer_only:
        assert not any(path in bullet for bullet in bullets)


def test_remediation_proof_is_separate_from_issue_grounding(judge) -> None:
    thread = _thread(judge)
    remediation = _example("taintcheck")["findings"][0]["remediation"]
    rationale = remediation["rationale"]
    illustration = remediation["illustration"].strip()
    reviewer_check = remediation["checks"][0]["observation"]
    scout_observation = "The sink exposes a parameter API."

    assert rationale in thread
    assert reviewer_check not in thread
    assert scout_observation in thread
    assert (
        thread.index("### Issue")
        < thread.index("**Grounding**")
        < thread.index("### Suggested remediation")
        < thread.index(rationale)
        < thread.index(illustration)
        < thread.index("**Support**")
    )
    assert all(rationale not in bullet for bullet in _grounding_bullets(thread))
    assert all(scout_observation not in bullet for bullet in _grounding_bullets(thread))


def test_grounding_never_repeats_a_span_it_already_covers(judge) -> None:
    """`38-44` contains `42`; the old substring dedup could not see that."""
    bullets = _grounding_bullets(_thread(judge))

    assert not any("38-44" in bullet for bullet in bullets)


def test_reviewer_anchors_still_position_the_thread_without_target_first_routing(judge) -> None:
    """The remediation illustration does not override the finding's normal anchor."""
    finding = _project(judge).all_findings[0]
    target = _example("taintcheck")["findings"][0]["anchors"][0]

    assert (finding.file_path, finding.start_line) == (target["filePath"], target["startLine"])


def test_counter_evidence_never_reaches_a_thread(judge) -> None:
    judge["claims"][0]["counter_evidence"] = [
        {
            "file": "src/data/SearchService.cs",
            "start_line": 12,
            "observation": "every caller pre-escapes the term",
        }
    ]

    assert "pre-escapes" not in _thread(judge)


def test_verdict_comment_states_what_the_change_does_before_ruling_on_it(judge) -> None:
    result = _project(judge)
    summary = result.commenter.render_summary(
        "session", result.verdict, result.all_findings, published=result.all_findings
    )
    intent = judge["verdict"]["intent"]

    assert intent in summary
    assert summary.index(intent) < summary.index(judge["verdict"]["summary"])


def test_a_claim_the_judge_did_not_ground_shows_no_grounding_section(judge) -> None:
    judge["claims"][0].update(disposition="insufficient_evidence", severity="medium", evidence=[])

    assert "**Grounding**" not in _thread(judge)
