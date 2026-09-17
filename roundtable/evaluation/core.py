"""Provider-neutral evaluation ordering and failure contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from roundtable.providers import RepositoryIdentity, RevisionProvenance


@dataclass(frozen=True)
class EvaluationTarget:
    repository: RepositoryIdentity
    revision: RevisionProvenance
    label: str


@dataclass(frozen=True)
class EvaluationReviewResult:
    session_dir: Path
    outcome: str
    exit_code: int


@dataclass(frozen=True)
class EvaluationChangeRequest:
    locator: str
    canonical_url: str


@dataclass(frozen=True)
class EvaluationRunResult:
    target: EvaluationTarget
    review: EvaluationReviewResult
    change_request: EvaluationChangeRequest
    publish_exit_code: int

    @property
    def complete(self) -> bool:
        return self.publish_exit_code == 0


class EvaluationProvider(Protocol):
    def stage(self, target: EvaluationTarget, name: str) -> object: ...

    def create_change_request(
        self,
        target: EvaluationTarget,
        staged: object,
        review: EvaluationReviewResult,
    ) -> EvaluationChangeRequest: ...

    def cleanup(self, name: str, *, dry_run: bool = False) -> object: ...


ReviewOperation = Callable[[EvaluationTarget], EvaluationReviewResult]
PublishOperation = Callable[[Path, EvaluationChangeRequest], int]


def evaluate(
    target: EvaluationTarget,
    *,
    name: str,
    provider: EvaluationProvider,
    review: ReviewOperation,
    publish: PublishOperation,
) -> EvaluationRunResult:
    """Review before any provider mutation, then stage, create, and publish."""
    reviewed = review(target)
    staged = provider.stage(target, name)
    change_request = provider.create_change_request(target, staged, reviewed)
    publish_exit_code = publish(reviewed.session_dir, change_request)
    return EvaluationRunResult(
        target=target,
        review=reviewed,
        change_request=change_request,
        publish_exit_code=publish_exit_code,
    )
