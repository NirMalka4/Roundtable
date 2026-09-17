"""Publishing regressions for claim-only Buddies adjudication."""

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
from roundtable.configs.buddies.plugins.claims import PUBLISHABLE_EFFECTS, ClaimContext
from roundtable.configs.buddies.plugins.claims_projector import ClaimsProjector
from roundtable.configs.buddies.plugins.commenter import (
    _BASIS_GLOSS,
    _DOMAINS,
    _EFFECTS,
    _location_note,
)
from roundtable.configs.buddies.plugins.verdict import RULING_MATRIX

BUNDLE = Path(buddies_pkg.__file__).parent
SESSION_DIR = "/artifacts/repo/session_example"
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


def _project(
    claims: list[dict[str, Any]],
    *,
    reviewers: dict[str, dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    include_scout: bool = True,
    session_dir: str | None = SESSION_DIR,
):
    judge = _example("judge")
    judge["claims"] = claims
    reviewer_outputs = reviewers or {"taintcheck": _example("taintcheck")}
    session = {
        **{key: {"response": json.dumps(value)} for key, value in reviewer_outputs.items()},
        "Judge": {"response": json.dumps(judge)},
    }
    if include_scout:
        session["RemedyScout"] = {
            "response": json.dumps(
                {"decisions": decisions if decisions is not None else _decisions(claims)}
            )
        }
    return ClaimsProjector().project(session, session_dir_path=session_dir)


def _thread(result, *, kind: str = "inline") -> str:
    finding = result.all_findings[0]
    decision = AnchorDecision(kind, finding.file_path, finding.start_line, finding.end_line, None)
    return result.commenter.render_thread(finding, decision, session_id="session")


def _summary(result, *, published=None) -> str:
    published = result.all_findings if published is None else published
    return result.commenter.render_summary(
        "session", result.verdict, result.all_findings, published=published
    )


def _checks() -> list[dict[str, Any]]:
    return [
        {
            "filePath": "src/data/SearchService.cs",
            "startLine": 42,
            "endLine": 42,
            "observation": "The proposed change keeps untrusted text out of SQL grammar.",
        }
    ]


def _decisions(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": claim["id"],
            "primary_source_finding_id": claim["primary_source_finding_id"],
            "decision": "publish",
            "reason": "The independently read sink contract supports this draft.",
            "evidence": [
                {
                    "source": "repository",
                    "path": "src/data/SearchService.cs",
                    "start_line": 42,
                    "end_line": 42,
                    "excerpt": "Query(sql, new { searchTerm });",
                    "observation": "The sink accepts a parameter object.",
                }
            ],
        }
        for claim in claims
        if claim.get("disposition") != "rejected"
    ]


@pytest.fixture
def claim() -> dict[str, Any]:
    return _example("judge")["claims"][0]


def test_presentation_tables_cover_claim_enums() -> None:
    schema = yaml.safe_load((BUNDLE / "schemas" / "judge.schema.yaml").read_text(encoding="utf-8"))
    props = schema["properties"]["claims"]["items"]["properties"]
    derived = {effect for cell in RULING_MATRIX.values() for effect in cell.values()}
    assert set(_EFFECTS) == derived - {"none"}
    assert set(PUBLISHABLE_EFFECTS) == derived - {"none"}
    assert set(_DOMAINS) == set(props["criterion"]["enum"])
    assert set(_BASIS_GLOSS) == {
        "clean",
        "nonblocking_findings",
        "proven_failure",
        "unresolved_blocker",
    }


# ── where the thread sits ───────────────────────────────────────────────────
def _cited(*items: tuple[str, int, int, str]) -> ClaimContext:
    return ClaimContext(
        claim={
            "evidence": [
                {"file": f, "start_line": s, "end_line": e, "observation": "o", "role": r}
                for f, s, e, r in items
            ]
        }
    )


_DEFECT = ("src/Comparer.cs", 14, 31, "defect")
_TRIGGER = ("src/Provider.cs", 691, 697, "trigger")


def test_a_thread_on_a_cited_line_stays_silent_about_location() -> None:
    """The reader is already standing on the evidence; a note would be noise."""
    decision = AnchorDecision("inline", "src/Provider.cs", 690, 700, None)
    assert _location_note(decision, _cited(_DEFECT, _TRIGGER)) == []


def test_a_thread_anchored_away_from_every_citation_says_where_the_defect_is() -> None:
    """A defect in unchanged code cannot carry an ADO thread, so the anchor is a
    changed line that merely exposes it. Unsignposted, the reader is left guessing."""
    decision = AnchorDecision("inline", "src/Provider.cs", 369, 376, None)
    note = _location_note(decision, _cited(_DEFECT, _TRIGGER))
    assert note and "src/Comparer.cs:14-31" in note[0]


def test_the_signpost_names_the_defect_not_merely_the_first_citation() -> None:
    decision = AnchorDecision("inline", "src/Provider.cs", 369, 376, None)
    note = _location_note(decision, _cited(_TRIGGER, _DEFECT))
    assert note and "src/Comparer.cs:14-31" in note[0]


def test_a_comment_that_never_got_a_line_still_explains_why() -> None:
    decision = AnchorDecision("general", "src/Comparer.cs", None, None, "file is not in the diff")
    note = _location_note(decision, _cited(_DEFECT))
    assert note == ["<sub>**Location:** src/Comparer.cs — file is not in the diff</sub>"]


def test_a_malformed_claim_is_not_reported_as_dismissed(claim: dict[str, Any]) -> None:
    """ "Dismissed on review" is a statement about the review. A claim whose cell derives
    no effect was never adjudicated away — it reaches no reader surface at all, and
    counting it as dismissed tells the reader a review happened that did not."""
    malformed = {**claim, "id": "J-99", "disposition": "upheld", "severity": "none"}
    result = _project([claim, malformed])
    assert result is not None
    assert "dismissed on review" not in _summary(result)
    assert "malformed=1" in result.log_summary


def test_a_genuinely_dismissed_claim_is_still_counted(claim: dict[str, Any]) -> None:
    """The positive definition must not silence the real case."""
    dismissed = {**claim, "id": "J-98", "disposition": "rejected", "severity": "none"}
    result = _project([claim, dismissed])
    assert result is not None
    assert "1 dismissed on review" in _summary(result)
    assert "malformed" not in result.log_summary


def test_thread_is_direct_grounded_and_free_of_review_bookkeeping(claim: dict[str, Any]) -> None:
    result = _project([claim])
    assert result is not None
    body = _thread(result)
    remediation = _example("taintcheck")["findings"][0]["remediation"]
    illustration = remediation["illustration"].strip()
    assert "## 🛡️ User input reaches an unparameterized search query" in body
    assert "### Issue" in body
    assert illustration in body
    assert "### Suggested remediation" in body
    assert f"{remediation['rationale']} {remediation['proposal']}" in body
    assert "**Rationale**" not in body
    assert "**Proposal**" not in body
    assert "**Support**" in body
    assert "The sink accepts a parameter object." in body
    assert remediation["checks"][0]["observation"] not in body
    assert "```suggestion" not in body
    assert "not verified by Judge" not in body
    assert "directionally_valid" not in body
    assert "**Grounding**" in body
    assert "**Why**" not in body
    assert "**Why this works**" not in body
    assert "**Checks**" not in body
    assert (
        body.index("### Issue")
        < body.index("**Grounding**")
        < body.index("### Suggested remediation")
        < body.index(f"{remediation['rationale']} {remediation['proposal']}")
        < body.index(illustration)
        < body.index("**Support**")
    )


def test_general_thread_uses_plain_fence_not_apply_change(claim: dict[str, Any]) -> None:
    result = _project([claim])
    body = _thread(result, kind="general")
    assert "```suggestion" not in body
    assert "```\n" in body


def test_accepted_draft_renders_limitations_and_an_ordinary_fence(
    claim: dict[str, Any],
) -> None:
    reviewer = _example("taintcheck")
    reviewer["findings"][0]["remediation"] = {
        "rationale": "The sink-level validation covers every caller without changing valid input.",
        "proposal": "Validate the route input, then use the existing query parameter API.",
        "illustration": "Query(sql, new { searchTerm });",
        "language": "csharp",
        "checks": _checks(),
        "limitations": ["The shared ownership boundary is unresolved."],
    }
    body = _thread(_project([claim], reviewers={"taintcheck": reviewer}))
    assert "### Suggested remediation" in body
    assert "**Known limitations**" in body
    remediation = reviewer["findings"][0]["remediation"]
    assert f"{remediation['rationale']} {remediation['proposal']}" in body
    assert "**Rationale**" not in body
    assert "**Proposal**" not in body
    assert "```csharp" in body
    assert "```suggestion" not in body
    assert body.index("**Support**") < body.index("**Known limitations**")


@pytest.mark.parametrize(
    ("decisions", "include_scout"),
    (
        (
            [
                {
                    "claim_id": "J-01",
                    "primary_source_finding_id": "taintcheck::TC-01",
                    "decision": "withhold",
                    "reason": "A material consumer remains unresolved.",
                    "evidence": [
                        {
                            "source": "draft",
                            "component": "limitations[0]",
                            "excerpt": "not executed",
                            "observation": "The draft discloses unresolved verification.",
                        }
                    ],
                }
            ],
            True,
        ),
        (None, False),
    ),
)
def test_withheld_or_unavailable_adjudication_omits_only_remediation(
    claim: dict[str, Any],
    decisions: list[dict[str, Any]] | None,
    include_scout: bool,
) -> None:
    result = _project(
        [claim],
        decisions=decisions,
        include_scout=include_scout,
    )
    body = _thread(result)
    assert "### Issue" in body
    assert claim["reason"] in body
    assert "Suggested remediation" not in body
    assert "withhold" not in body.lower()


def test_dismissed_claim_is_not_published(claim: dict[str, Any]) -> None:
    dismissed = {**claim, "disposition": "rejected", "severity": "none"}
    result = _project([dismissed])
    assert result is not None
    assert result.all_findings == []
    assert "dismissed=1" in result.log_summary


def test_primary_source_controls_anchor_and_remediation(claim: dict[str, Any]) -> None:
    taint = _example("taintcheck")
    smell = copy.deepcopy(taint)
    smell_finding = smell["findings"][0]
    smell_finding["id"] = "SC-01"
    smell_finding["anchors"][0].update(filePath="src/other.cs", startLine=70, endLine=70)
    smell_finding["remediation"] = {
        "rationale": "The bounded helper removes the duplicated decision at its owner.",
        "proposal": "Use the bounded helper at the primary call site.",
        "illustration": "bounded(value)",
        "checks": _checks(),
        "limitations": ["The owning layer still requires an author decision."],
    }
    combined = {
        **claim,
        "source_finding_ids": ["taintcheck::TC-01", "smellcheck::SC-01"],
        "primary_source_finding_id": "smellcheck::SC-01",
    }
    result = _project(
        [combined],
        reviewers={"taintcheck": taint, "smellcheck": smell},
    )
    assert result.all_findings[0].file_path == "src/other.cs"
    body = _thread(result)
    assert "Use the bounded helper at the primary call site." in body
    assert "Taint Check (TC-01)" in body
    assert "Smell Check (SC-01)" in body


def test_invalid_primary_source_does_not_fall_back_to_a_remediation(
    claim: dict[str, Any],
) -> None:
    invalid = {**claim, "primary_source_finding_id": "smellcheck::SC-99"}
    body = _thread(_project([invalid]))
    assert "Suggested remediation" not in body


def test_stable_identity_changes_with_wording_primary_source_and_payload(
    claim: dict[str, Any],
) -> None:
    original = _project([claim]).all_findings[0].stable_hash
    reworded = _project([{**claim, "reason": "Different reason."}]).all_findings[0].stable_hash
    reviewer = _example("taintcheck")
    reviewer["findings"][0]["remediation"]["illustration"] += "\n// changed"
    repayloaded = _project([claim], reviewers={"taintcheck": reviewer}).all_findings[0].stable_hash
    rerationalized = _example("taintcheck")
    rerationalized["findings"][0]["remediation"]["rationale"] = "A different grounded rationale."
    rationale_hash = (
        _project([claim], reviewers={"taintcheck": rerationalized}).all_findings[0].stable_hash
    )
    changed_decision = _decisions([claim])
    changed_decision[0]["reason"] = "A different evidence-derived reason."
    decision_hash = _project([claim], decisions=changed_decision).all_findings[0].stable_hash
    changed_evidence = _decisions([claim])
    changed_evidence[0]["evidence"][0]["observation"] = "A different visible observation."
    evidence_hash = _project([claim], decisions=changed_evidence).all_findings[0].stable_hash
    withheld = copy.deepcopy(changed_decision)
    withheld[0]["decision"] = "withhold"
    withheld_hash = _project([claim], decisions=withheld).all_findings[0].stable_hash
    assert decision_hash == original
    assert len({original, reworded, repayloaded, rationale_hash, evidence_hash, withheld_hash}) == 6


def test_summary_derives_basis_and_preserves_judge_summary(claim: dict[str, Any]) -> None:
    result = _project([claim])
    summary = _summary(result)
    assert "REJECT — a claim was upheld and it breaks the release" in summary
    assert _example("judge")["verdict"]["summary"] in summary


def test_summary_discloses_missing_reviewers(claim: dict[str, Any]) -> None:
    assert "Degraded coverage" in _summary(_project([claim]))


def test_comment_has_no_machine_local_path(claim: dict[str, Any]) -> None:
    body = _thread(_project([claim]))
    assert SESSION_DIR not in body
    assert "C:\\" not in body
