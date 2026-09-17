from pathlib import Path

from roundtable.evaluation import (
    EvaluationChangeRequest,
    EvaluationReviewResult,
    EvaluationTarget,
    evaluate,
)
from roundtable.providers import ProviderId, RepositoryIdentity, RevisionProvenance


def test_generic_evaluation_reviews_before_provider_mutation(tmp_path: Path) -> None:
    events: list[str] = []
    target = EvaluationTarget(
        repository=RepositoryIdentity(
            ProviderId("fake"),
            "example.test",
            "repo",
            "repo",
        ),
        revision=RevisionProvenance("source", "base"),
        label="test",
    )

    class Provider:
        def stage(self, _target, _name):
            events.append("stage")
            return "staged"

        def create_change_request(self, _target, _staged, _review):
            events.append("create")
            return EvaluationChangeRequest("1", "https://example.test/change/1")

        def cleanup(self, _name, *, dry_run=False):
            return None

    result = evaluate(
        target,
        name="test",
        provider=Provider(),
        review=lambda _target: (
            events.append("review") or EvaluationReviewResult(tmp_path, "REJECT", 2)
        ),
        publish=lambda _session, _change: events.append("publish") or 0,
    )

    assert events == ["review", "stage", "create", "publish"]
    assert result.complete
