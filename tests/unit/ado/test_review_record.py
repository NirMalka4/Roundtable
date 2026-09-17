from __future__ import annotations

import json
from types import SimpleNamespace

from roundtable.ado.review_record import (
    RECORD_FILENAME,
    build_review_record,
    record_review,
    retry_record,
)
from roundtable.adoption import ReviewRecord
from roundtable.inputs import PrReference


def _record() -> ReviewRecord:
    return ReviewRecord(
        session_id="session-1",
        recorded_at="2026-09-09T00:00:00Z",
        organization="contoso",
        project="ExampleProject",
        repository="Repo",
        pull_request_id=42,
        source_sha="s" * 40,
        base_sha="b" * 40,
        tool_version="4.6.3",
        configuration_name="buddies",
        configuration_kind="shipped",
        graph_config_sha="abcdef123456",
        installation_source="feed",
        verdict="APPROVE",
        findings_available=True,
        counts=(("all", 1),),
        findings=(),
    )


def _pr() -> PrReference:
    return PrReference("contoso", "ExampleProject", "ExampleRepo", 42)


class _Labels:
    def __init__(self, action: str = "added") -> None:
        self.labels = []
        self.action = action

    def apply_adoption_label(self, label: str) -> str:
        self.labels[:] = [label]
        return self.action


def test_record_persists_metadata_and_labels_without_posting_a_comment(tmp_path) -> None:
    labels = _Labels()

    report = record_review(
        _record(),
        _pr(),
        tmp_path,
        label_client_factory=lambda _pr: labels,
    )

    assert report.ok
    assert report.summary is None
    assert labels.labels == ["Roundtable-v1-4.6.3-buddies-feed"]
    state = json.loads((tmp_path / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert state["status"] == "complete"
    assert state["summary"] is None
    assert state["record"] == _record().to_dict()


def test_recording_failure_persists_retryable_intent(tmp_path) -> None:
    report = record_review(
        _record(),
        _pr(),
        tmp_path,
        label_client_factory=lambda _pr: _Labels("failed"),
    )

    state = json.loads((tmp_path / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert not report.ok
    assert state["status"] == "failed"
    assert state["record"]["sessionId"] == "session-1"
    assert state["failureReason"] == "label recording failed"


def test_retry_reuses_persisted_record_and_is_idempotent(tmp_path) -> None:
    record_review(
        _record(),
        _pr(),
        tmp_path,
        label_client_factory=lambda _pr: _Labels("failed"),
    )
    labels = _Labels()

    report = retry_record(
        tmp_path,
        label_client_factory=lambda _pr: labels,
    )

    assert report.ok
    assert report.summary is None
    assert labels.labels == ["Roundtable-v1-4.6.3-buddies-feed"]


def test_projectorless_external_configuration_records_partial(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(
        scheduler=SimpleNamespace(results={}),
        persist=SimpleNamespace(session_dir=tmp_path / "session-1"),
        verdict=SimpleNamespace(verdict="APPROVE"),
        counts={"all": 3},
    )
    configuration = SimpleNamespace(
        identity={"kind": "external"},
        projector=None,
        name="custom",
        fingerprint="abc",
    )

    record = build_review_record(
        result,
        _pr(),
        {"sourceSha": "s", "baseSha": "b"},
        configuration,
        source="local",
    )

    assert not record.findings_available
    assert record.findings == ()
    assert record.counts == (("all", 3),)


def test_configured_projector_supplies_normalized_findings(monkeypatch, tmp_path) -> None:
    finding = SimpleNamespace(
        id="RG-1",
        severity="medium",
        category="non_blocking",
        source_agents=("redgreen", "api"),
    )
    projector = SimpleNamespace(
        project=lambda *_args, **_kwargs: SimpleNamespace(
            all_findings=[finding],
            counts={"all": 1},
        )
    )
    monkeypatch.setattr("roundtable.ado.review_record.get_projector", lambda _name: projector)
    result = SimpleNamespace(
        scheduler=SimpleNamespace(results={}),
        persist=SimpleNamespace(session_dir=tmp_path / "session-1"),
        verdict=SimpleNamespace(verdict="APPROVE_WITH_SUGGESTIONS"),
        counts={"all": 9},
    )
    configuration = SimpleNamespace(
        identity={"kind": "shipped"},
        projector="claims",
        name="buddies",
        fingerprint="abc",
    )

    record = build_review_record(
        result,
        _pr(),
        {"sourceSha": "s", "baseSha": "b"},
        configuration,
        source="feed",
    )

    assert record.findings_available
    assert record.findings[0].agents == ("api", "redgreen")
    assert record.counts == (("all", 1),)
