from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import eval_recall

ORACLE = {
    "id": "binding-ambiguity",
    "patterns": ["ambigu", "WidgetId"],
}


def _session(tmp_path: Path, results: dict[str, object], name: str = "session") -> Path:
    session = tmp_path / name
    session.mkdir()
    encoded = {key: {"response": json.dumps(value)} for key, value in results.items()}
    (session / "raw_results.json").write_text(json.dumps(encoded), encoding="utf-8")
    return session


def _oracle_file(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps(payload or ORACLE), encoding="utf-8")
    return path


def _finding(**overrides: object) -> dict[str, object]:
    finding = {"id": "CC-02", "severity": "high", "title": "WidgetId is ambiguous once joined"}
    finding.update(overrides)
    return finding


def _score(
    tmp_path: Path,
    results: dict[str, object],
    payload: dict[str, object] | None = None,
    name: str = "session",
):
    oracle = eval_recall.Oracle.from_payload(payload or ORACLE, label="test")
    return eval_recall.score_session(_session(tmp_path, results, name), oracle)


def test_a_claim_no_judge_published_is_raised_but_unpublished(tmp_path) -> None:
    score = _score(tmp_path, {"countercase": {"findings": [_finding()]}, "Judge": {"claims": []}})
    assert score.outcome == "raised_not_published"
    assert [(h["agent"], h["findingId"]) for h in score.reviewer_hits] == [("countercase", "CC-02")]


def test_an_upheld_blocker_claim_is_the_published_outcome(tmp_path) -> None:
    score = _score(
        tmp_path,
        {
            "countercase": {"findings": [_finding()]},
            "Judge": {
                "claims": [
                    {
                        "id": "J-01",
                        "disposition": "upheld",
                        "effect": "blocker",
                        "reason": "WidgetId is ambiguous",
                        "source_finding_ids": ["countercase::CC-02"],
                    }
                ]
            },
        },
    )
    assert score.outcome == "published_blocker"
    assert score.judge_hits[0]["sourceFindingIds"] == ["countercase::CC-02"]


def test_a_claim_the_judge_downgrades_is_not_a_published_blocker(tmp_path) -> None:
    score = _score(
        tmp_path,
        {
            "countercase": {"findings": [_finding()]},
            "Judge": {
                "claims": [
                    {
                        "id": "J-01",
                        "disposition": "upheld",
                        "effect": "suggestion",
                        "reason": "WidgetId is ambiguous",
                    }
                ]
            },
        },
    )
    assert score.outcome == "published_downgraded"


def test_high_severity_is_the_top_grade_once_effect_is_gone(tmp_path) -> None:
    """The Judge schema replaced `effect: blocker` with `severity: high`."""
    score = _score(
        tmp_path,
        {
            "countercase": {"findings": [_finding()]},
            "Judge": {
                "claims": [
                    {
                        "id": "J-01",
                        "disposition": "upheld",
                        "severity": "high",
                        "reason": "WidgetId is ambiguous",
                    }
                ]
            },
        },
    )
    assert score.outcome == "published_blocker"


def test_a_claim_is_carried_by_its_source_link_not_its_wording(tmp_path) -> None:
    """The Judge restates a finding in terser words; provenance is the reliable link."""
    score = _score(
        tmp_path,
        {
            "countercase": {"findings": [_finding()]},
            "Judge": {
                "claims": [
                    {
                        "id": "J-01",
                        "disposition": "upheld",
                        "severity": "high",
                        "reason": "the call aborts instead of returning a count",
                        "primary_source_finding_id": "countercase::CC-02",
                    }
                ]
            },
        },
    )
    assert score.outcome == "published_blocker"
    assert score.judge_hits[0]["matchedBy"] == "attribution"


def test_a_source_link_to_an_unrelated_finding_is_not_a_hit(tmp_path) -> None:
    score = _score(
        tmp_path,
        {
            "countercase": {"findings": [_finding(id="CC-09", title="an unrelated retry loop")]},
            "Judge": {
                "claims": [
                    {
                        "id": "J-01",
                        "disposition": "upheld",
                        "severity": "high",
                        "reason": "the retry loop never terminates",
                        "primary_source_finding_id": "countercase::CC-09",
                    }
                ]
            },
        },
    )
    assert score.outcome == "missed"
    assert score.judge_hits == []


def test_quoted_source_alone_is_not_a_claim(tmp_path) -> None:
    """An excerpt reads the same for every agent, so anchoring a file is not asserting a defect."""
    quoted = _finding(
        title="Duplicated authorization rule",
        anchors=[{"filePath": "helper.sql", "excerpt": "-- avoid ambiguity with WidgetId"}],
    )
    score = _score(tmp_path, {"countercase": {"findings": [quoted]}})
    assert score.outcome == "missed"
    assert score.reviewer_hits == []


def test_a_rejected_counter_argument_is_not_a_claim(tmp_path) -> None:
    """counterevidence holds the argument the finding argues against, not what it asserts."""
    rejected = _finding(
        title="Value is dropped on write",
        counterevidence="The strongest counter-case is that WidgetId exists only to resolve ambiguity.",
    )
    score = _score(tmp_path, {"countercase": {"findings": [rejected]}})
    assert score.outcome == "missed"


def test_findings_outside_the_named_agents_do_not_count(tmp_path) -> None:
    results = {"redgreen": {"findings": [_finding(id="RG-01")]}}
    scoped = _score(tmp_path, results, {**ORACLE, "agents": ["countercase"]}, name="scoped")
    assert scoped.outcome == "missed"
    unscoped = _score(tmp_path, results, name="unscoped")
    assert unscoped.outcome == "raised_not_published"


def test_a_dropped_candidate_is_reported_without_counting_as_a_claim(tmp_path) -> None:
    score = _score(
        tmp_path,
        {
            "countercase": {
                "findings": [],
                "abstentions": [
                    {
                        "subject": "WidgetId may be ambiguous",
                        "kind": "counter_case_held",
                        "reason": "the alias removes it",
                    }
                ],
            }
        },
    )
    assert score.outcome == "missed"
    assert score.abstention_near_misses[0]["kind"] == "counter_case_held"
    assert score.abstention_near_misses[0]["hasResolvedArtifact"] is False
    assert any("dropped" in note for note in score.notes)


def test_a_near_miss_survives_one_missing_element_but_not_two(tmp_path) -> None:
    """Agents describe a mechanism without naming every element, so a warning needs slack."""
    oracle = {**ORACLE, "patterns": ["ambigu", "WidgetId", r"\b(bare|unqualified)\b"]}
    near = {"subject": "alias change", "reason": "it removes the bare-column ambiguity"}
    far = {"subject": "unrelated", "reason": "the WidgetId column is unpopulated"}
    score = _score(
        tmp_path,
        {"countercase": {"findings": [], "abstentions": [near, far]}},
        oracle,
    )
    assert [a["subject"] for a in score.abstention_near_misses] == ["alias change"]


def test_a_near_miss_is_never_enough_for_a_finding(tmp_path) -> None:
    partial = _finding(title="the bare column is ambiguous")
    score = _score(tmp_path, {"countercase": {"findings": [partial]}})
    assert score.outcome == "missed"


def test_every_pattern_must_appear(tmp_path) -> None:
    partial = _finding(title="The join is ambiguous")
    assert _score(tmp_path, {"countercase": {"findings": [partial]}}).outcome == "missed"


@pytest.mark.parametrize(
    "payload",
    [{"patterns": []}, {"patterns": ["("]}, {"patterns": ["ok"], "agents": "countercase"}],
)
def test_an_unusable_oracle_is_rejected(payload) -> None:
    with pytest.raises(eval_recall.OracleError):
        eval_recall.Oracle.from_payload(payload, label="test")


def test_expect_fails_the_command_when_a_session_misses(tmp_path) -> None:
    session = _session(tmp_path, {"countercase": {"findings": []}})
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/eval_recall.py",
            "--oracle",
            str(_oracle_file(tmp_path)),
            "--session",
            str(session),
            "--expect",
            "published_blocker",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["tally"] == {"missed": 1}


def test_an_incomplete_session_is_rejected(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/eval_recall.py",
            "--oracle",
            str(_oracle_file(tmp_path)),
            "--session",
            str(tmp_path / "absent"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "not a complete session" in completed.stderr
