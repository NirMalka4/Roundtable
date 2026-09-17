"""Session-report regressions for the claim-only Judge contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import roundtable.configs.buddies as buddies_pkg
from roundtable.configs.buddies.plugins.report import ClaimsReport
from roundtable.configs.buddies.plugins.verdict import derive_verdict
from roundtable.decision.verdict import VerdictResult

BUNDLE = Path(buddies_pkg.__file__).parent


class _Entry:
    def __init__(self, key: str) -> None:
        self.key = key


class _Cfg:
    name = "buddies"
    entries = tuple(_Entry(k) for k in ("taintcheck", "smellcheck", "Judge", "RemedyScout"))


class _Outcome:
    def __init__(self, payload: Any) -> None:
        self.response = payload if isinstance(payload, str) else json.dumps(payload)
        self.valid = True


def _example(name: str) -> dict[str, Any]:
    schema = yaml.safe_load(
        (BUNDLE / "schemas" / f"{name}.schema.yaml").read_text(encoding="utf-8")
    )
    return copy.deepcopy(schema["examples"][0])


@pytest.fixture(autouse=True)
def buddies_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import roundtable.configs.buddies.plugins.judge_output as judge_output_mod
    import roundtable.configs.buddies.plugins.report as report_mod
    from roundtable.configs.buddies.plugins import coverage as coverage_mod

    monkeypatch.setattr(judge_output_mod, "buddies_configuration", lambda: _Cfg())
    monkeypatch.setattr(report_mod, "get_product_name", lambda: "Buddies")
    monkeypatch.setattr(report_mod, "get_agent_display_name", lambda key: key.upper())
    monkeypatch.setattr(coverage_mod, "_reviewer_keys", lambda: ("taintcheck",))
    monkeypatch.setattr(coverage_mod, "get_agent_display_name", lambda key, *_: key.upper())


def _decision(payload: dict[str, Any], kind: str = "publish") -> dict[str, Any]:
    claim = payload["claims"][0]
    evidence = (
        [
            {
                "source": "repository",
                "path": "src/data/SearchService.cs",
                "start_line": 42,
                "end_line": 42,
                "excerpt": "command.Parameters.AddWithValue",
                "observation": "The sink exposes a parameter API.",
            }
        ]
        if kind == "publish"
        else [
            {
                "source": "draft",
                "component": "limitations[0]",
                "excerpt": "not executed",
                "observation": "The draft leaves verification unresolved.",
            }
        ]
    )
    return {
        "claim_id": claim["id"],
        "primary_source_finding_id": claim["primary_source_finding_id"],
        "decision": kind,
        "reason": (
            "The independently read sink contract supports the draft."
            if kind == "publish"
            else "A material verification boundary remains unresolved."
        ),
        "evidence": evidence,
    }


def _render(
    payload: dict[str, Any],
    *,
    include_scout: bool = True,
    decisions: list[dict[str, Any]] | None = None,
    **outputs: Any,
) -> str:
    verdict, counts = derive_verdict(payload)
    if include_scout:
        outputs["RemedyScout"] = {
            "decisions": decisions if decisions is not None else [_decision(payload)]
        }
    session = {"Judge": _Outcome(payload), **{k: _Outcome(v) for k, v in outputs.items()}}
    rendered = ClaimsReport().render(
        verdict,
        counts,
        session,
        repo_name="Contoso.Api",
        source_branch="user/feature",
        session_id="session",
    )
    assert rendered is not None
    return rendered.markdown


def test_report_reads_judge_by_shape_not_name() -> None:
    payload = _example("judge")
    verdict, counts = derive_verdict(payload)
    rendered = ClaimsReport().render(
        verdict,
        counts,
        {"taintcheck": _Outcome(payload)},
        repo_name=None,
        source_branch=None,
        session_id="session",
    )
    assert rendered is not None
    assert "J-01" in rendered.markdown


def test_no_judge_output_declines_to_render() -> None:
    verdict = VerdictResult("UNKNOWN", "?", False, "", "session")
    assert (
        ClaimsReport().render(
            verdict, None, {}, repo_name=None, source_branch=None, session_id="session"
        )
        is None
    )


def test_header_uses_derived_basis_and_counts() -> None:
    md = _render(_example("judge"))
    assert md.startswith("# ")
    assert "REJECT" in md.splitlines()[0]
    assert "Proven failure" in md
    assert "2 blocking" in md
    assert "1 security" in md


def test_report_names_reviewed_subject_and_summary() -> None:
    payload = _example("judge")
    md = _render(payload)
    assert "Contoso.Api" in md
    assert "user/feature" in md
    assert payload["verdict"]["summary"] in md


def test_report_renders_claim_evidence_and_primary_reviewer_remediation() -> None:
    payload = _example("judge")
    reviewer = _example("taintcheck")
    md = _render(payload, taintcheck=reviewer)
    illustration = reviewer["findings"][0]["remediation"]["illustration"].strip()
    remediation = reviewer["findings"][0]["remediation"]
    source_id = payload["claims"][0]["primary_source_finding_id"]
    expected = (
        f"**Suggested remediation** — `{source_id}`\n\n"
        f"{remediation['rationale']} {remediation['proposal']}\n\n"
        f"```{remediation['language']}\n{illustration}\n```\n\n"
        "**Support**\n\n"
        "- `src/data/SearchService.cs:42` - The sink exposes a parameter API. "
        "(`command.Parameters.AddWithValue`)\n"
    )
    assert payload["claims"][0]["reason"] in md
    assert payload["claims"][0]["evidence"][0]["observation"] in md
    assert expected in md
    assert "**Rationale**" not in md
    assert "**Proposal**" not in md
    assert "**Reviewer proposal (withheld)**" not in md
    assert "**Why Remedy Scout withheld it**" not in md
    assert remediation["checks"][0]["observation"] not in md
    assert (
        md.index("**Suggested remediation**")
        < md.index(f"{remediation['rationale']} {remediation['proposal']}")
        < md.index(illustration)
        < md.index("**Support**")
    )
    assert "directionally valid" not in md.lower()


def test_report_records_counter_evidence_the_thread_withholds() -> None:
    """The durable record keeps what argued the other way; the PR thread does not."""
    payload = _example("judge")
    payload["claims"][0]["counter_evidence"] = [
        {
            "file": "src/data/SearchService.cs",
            "start_line": 12,
            "observation": "every caller pre-escapes the term",
        }
    ]
    md = _render(payload, taintcheck=_example("taintcheck"))
    assert "Counter-evidence" in md
    assert "every caller pre-escapes the term" in md


def test_report_renders_known_remediation_limitations_conditionally() -> None:
    payload = _example("judge")
    reviewer = _example("taintcheck")
    remediation = reviewer["findings"][0]["remediation"]
    remediation["limitations"] = ["The shared ownership boundary is unresolved."]

    md = _render(payload, taintcheck=reviewer)

    assert "**Known limitations**" in md
    assert "The shared ownership boundary is unresolved." in md


def test_report_records_rejected_proposal_and_scout_reasoning_for_withhold() -> None:
    payload = _example("judge")
    reviewer = _example("taintcheck")
    remediation = reviewer["findings"][0]["remediation"]
    withheld = _render(
        payload,
        taintcheck=reviewer,
        decisions=[_decision(payload, "withhold")],
    )
    source_id = payload["claims"][0]["primary_source_finding_id"]
    assert f"**Remediation withheld** — `{source_id}`" in withheld
    assert "withheld by Remedy Scout; not an approved action" in withheld
    assert "**Suggested remediation**" not in withheld
    assert "**Reviewer proposal (withheld)**" in withheld
    assert f"{remediation['rationale']} {remediation['proposal']}" in withheld
    assert "**Rationale**" not in withheld
    assert "**Proposal**" not in withheld
    assert f"```{remediation['language']}\n{remediation['illustration']}\n```" in withheld
    assert (
        "**Why Remedy Scout withheld it**\n\nA material verification boundary remains unresolved."
    ) in withheld
    assert (
        "**Withholding evidence**\n\n"
        "- `draft:limitations[0]` - The draft leaves verification unresolved. "
        "(`not executed`)"
    ) in withheld
    assert (
        withheld.index("**Remediation withheld**")
        < withheld.index("**Reviewer proposal (withheld)**")
        < withheld.index("**Why Remedy Scout withheld it**")
        < withheld.index("**Withholding evidence**")
    )


def test_report_keeps_unavailable_remediation_fail_closed() -> None:
    payload = _example("judge")
    reviewer = _example("taintcheck")
    unavailable = _render(
        payload,
        taintcheck=reviewer,
        include_scout=False,
    )
    assert "**Remediation withheld**" in unavailable
    assert "adjudication was unavailable or invalid" in unavailable
    assert reviewer["findings"][0]["remediation"]["proposal"] not in unavailable
    assert "**Reviewer proposal (withheld)**" not in unavailable


def test_report_states_the_change_intent_above_the_ruling() -> None:
    payload = _example("judge")
    md = _render(payload, taintcheck=_example("taintcheck"))
    assert payload["verdict"]["intent"] in md
    assert md.index(payload["verdict"]["intent"]) < md.index(payload["verdict"]["summary"])


def test_report_groups_dismissed_claims_without_publishing_them_as_actions() -> None:
    payload = _example("judge")
    upheld = next(c for c in payload["claims"] if c["disposition"] == "upheld")
    dismissed = {
        **upheld,
        "id": "J-99",
        "disposition": "rejected",
        "severity": "none",
        "reason": "The cited guard blocks the path.",
    }
    payload["claims"] = [upheld, dismissed]
    md = _render(payload, taintcheck=_example("taintcheck"))
    assert "Dismissed claims (1)" in md
    assert "The cited guard blocks the path." in md


def test_report_includes_reviewer_intent_and_footer() -> None:
    md = _render(_example("judge"), taintcheck=_example("taintcheck"))
    assert "Agent understandings" in md
    assert "Generated by Buddies" in md
    assert "session `session`" in md


def test_unrenderable_claim_fails_loudly() -> None:
    payload = _example("judge")
    payload["claims"][0]["severity"] = "mystery"
    with pytest.raises(ValueError, match=r"not an adjudication cell"):
        _render(payload)
