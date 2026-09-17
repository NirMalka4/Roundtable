"""Referential integrity and evidence validation for remediation decisions."""

from __future__ import annotations

import json

import pytest

from roundtable.configs.buddies.plugins.gates import remediation_decisions_gate
from roundtable.validation.gate_kit import GateRequest

PAIR = ("J-01", "taintcheck::TC-01")
REMEDIATION = {
    "rationale": "The boundary owns escaping.",
    "proposal": "Use the parameter API at the shared sink.",
    "illustration": "query(sql, params)",
    "checks": [
        {
            "filePath": "src/a.py",
            "startLine": 8,
            "endLine": 8,
            "observation": "The sink accepts parameters.",
        }
    ],
    "limitations": ["The migration remains staged."],
}
DOSSIER = {
    "eligible_remediations": [
        {
            "claim": {"id": PAIR[0]},
            "primary_source_finding_id": PAIR[1],
            "reviewer_finding": {"id": "TC-01", "remediation": REMEDIATION},
        }
    ]
}


def _request(decisions: list[dict], *, calls: list[dict] | None = None) -> GateRequest:
    return GateRequest(
        "RemedyScout",
        {"decisions": decisions},
        {
            "upstream_responses": {"RemediationInputs": json.dumps(DOSSIER)},
            "tool_calls": calls or [],
        },
    )


def _decision(kind: str = "withhold") -> dict:
    return {
        "claim_id": PAIR[0],
        "primary_source_finding_id": PAIR[1],
        "decision": kind,
        "reason": "The cited evidence establishes the material boundary.",
        "evidence": [
            {
                "source": "draft",
                "component": "limitations[0]",
                "excerpt": "migration remains staged",
                "observation": "The migration is explicitly unresolved.",
            }
        ],
    }


def _messages(decisions: list[dict], **kwargs) -> list[str]:
    return [item.message for item in remediation_decisions_gate(_request(decisions, **kwargs))]


def test_withhold_accepts_an_exact_draft_component_citation() -> None:
    assert _messages([_decision()]) == []


def test_publish_requires_independently_read_repository_evidence() -> None:
    decision = _decision("publish")
    decision["evidence"] = [
        {
            "source": "repository",
            "path": "src/a.py",
            "start_line": 8,
            "end_line": 8,
            "excerpt": "query(sql, params)",
            "observation": "The shared sink accepts parameters.",
        }
    ]
    assert (
        _messages(
            [decision],
            calls=[
                {
                    "name": "view",
                    "args": {"path": "src/a.py", "view_range": [8, 8]},
                    "ok": True,
                    "output": "def sink():\n    query(sql, params)\n",
                }
            ],
        )
        == []
    )


@pytest.mark.parametrize(
    ("decisions", "expected"),
    (
        ([], "missing remediation decision"),
        ([_decision(), _decision()], "duplicate remediation decision"),
        (
            [
                {
                    **_decision(),
                    "primary_source_finding_id": "taintcheck::TC-99",
                }
            ],
            "does not reference a supplied eligible remediation",
        ),
    ),
)
def test_decisions_cover_each_supplied_pair_exactly_once(
    decisions: list[dict], expected: str
) -> None:
    assert any(expected in message for message in _messages(decisions))


def test_publish_rejects_reviewer_checks_and_draft_text_as_repository_proof() -> None:
    decision = _decision("publish")
    assert any(
        "independently corroborated repository evidence" in message
        for message in _messages([decision])
    )


@pytest.mark.parametrize(
    "args",
    (
        {"path": "src/a.py"},
        {"path": "src/a.py", "view_range": [7, 8]},
        {"path": "src/a.py", "view_range": [8, 9]},
        {"path": "src/other.py", "view_range": [8, 8]},
    ),
)
def test_repository_evidence_requires_an_exact_targeted_view(args: dict) -> None:
    decision = _decision("publish")
    decision["evidence"] = [
        {
            "source": "repository",
            "path": "src/a.py",
            "start_line": 8,
            "end_line": 8,
            "excerpt": "query(sql, params)",
            "observation": "The shared sink accepts parameters.",
        }
    ]
    messages = _messages(
        [decision],
        calls=[
            {
                "name": "view",
                "args": args,
                "ok": True,
                "output": "query(sql, params)",
            }
        ],
    )
    assert any("exact-range view" in message for message in messages)


def test_repository_evidence_rejects_discovery_or_remote_output() -> None:
    decision = _decision("publish")
    decision["evidence"] = [
        {
            "source": "repository",
            "path": "src/a.py",
            "start_line": 8,
            "end_line": 8,
            "excerpt": "query(sql, params)",
            "observation": "The shared sink accepts parameters.",
        }
    ]
    for name in ("grep", "ado-code-read/repo_get_file_content"):
        messages = _messages(
            [decision],
            calls=[
                {
                    "name": name,
                    "args": {"path": "src/a.py", "view_range": [8, 8]},
                    "ok": True,
                    "output": "query(sql, params)",
                }
            ],
        )
        assert any("exact-range view" in message for message in messages)


def test_context_free_schema_examples_defer_referential_checks() -> None:
    request = GateRequest("RemedyScout", {"decisions": [_decision()]}, {})
    assert remediation_decisions_gate(request) == []
