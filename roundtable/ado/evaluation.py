"""Azure DevOps exact-diff evaluation orchestration."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roundtable.ado_client import ado_auth_header, build_ado_base_url
from roundtable.inputs import run_git


class EvaluationError(RuntimeError):
    """An evaluation workflow stage could not complete."""


@dataclass(frozen=True)
class EvaluationRepository:
    org: str
    project: str
    name: str
    host: str
    remote_url: str


@dataclass(frozen=True)
class EvaluationRevision:
    repository: EvaluationRepository
    source_sha: str
    base_sha: str
    source_label: str
    mode: str = "explicit-base"
    requested_sha: str | None = None
    source_tip_sha: str | None = None
    resolved_pr_id: int | None = None
    merge_commit_sha: str | None = None


@dataclass(frozen=True)
class EvaluationReview:
    session_dir: Path
    verdict: str
    exit_code: int


@dataclass(frozen=True)
class DraftPullRequest:
    pr_id: int
    url: str


@dataclass(frozen=True)
class EvaluationOutcome:
    revision: EvaluationRevision
    review: EvaluationReview
    draft: DraftPullRequest
    source_ref: str
    target_ref: str
    publish_exit_code: int

    @property
    def complete(self) -> bool:
        return self.publish_exit_code == 0


ReviewOperation = Callable[[EvaluationRevision], EvaluationReview]
PushOperation = Callable[[EvaluationRevision, str, str], None]
CreateOperation = Callable[
    [EvaluationRevision, str, str, EvaluationReview],
    DraftPullRequest,
]
PublishOperation = Callable[[Path, str], int]

EVALUATION_REF_PREFIX = "refs/heads/roundtable/eval/"

# Deleting a branch is far slower than reading one: observed at 83s on a large Azure
# DevOps repository, against a 30s default that failed three attempts in a row. The
# operation is idempotent, so waiting costs nothing a retry would have saved.
REF_DELETE_TIMEOUT = 300.0


def _run_git(
    args: list[str],
    cwd: str,
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> Any:
    facade = sys.modules.get("roundtable.evaluation")
    operation = getattr(facade, "run_git", run_git)
    return operation(args, cwd, timeout=timeout, check=check)


@dataclass(frozen=True)
class TeardownTarget:
    """One evaluation's reclaimable remote state."""

    name: str
    source_ref: str
    target_ref: str


@dataclass(frozen=True)
class TeardownOutcome:
    """What a teardown abandoned and deleted, and what was already gone."""

    abandoned_pr_ids: tuple[int, ...]
    deleted_refs: tuple[str, ...]
    absent_refs: tuple[str, ...]
    dry_run: bool


DiscoverRefsOperation = Callable[[], tuple[str, ...]]
DiscoverPullRequestsOperation = Callable[[str], tuple[int, ...]]
AbandonOperation = Callable[[int], None]
DeleteRefOperation = Callable[[str], bool]


def ensure_evaluation_ref(ref: str) -> str:
    """Refuse to act on anything outside the disposable evaluation namespace.

    Checked here rather than trusted from the server-side ref filter that produced
    it, and re-checked for every ref a sweep enumerates. Teardown deletes branches in
    a shared repository, so the one invariant that makes it safe cannot depend on a
    caller having passed the right filter.
    """
    if not ref.startswith(EVALUATION_REF_PREFIX):
        raise EvaluationError(f"refusing to delete {ref!r}: outside {EVALUATION_REF_PREFIX}")
    return ref


def teardown_targets(names: Sequence[str]) -> tuple[TeardownTarget, ...]:
    """Build guarded teardown targets from caller-visible evaluation names."""
    targets = []
    for name in names:
        source_ref, target_ref = evaluation_refs(name)
        targets.append(
            TeardownTarget(
                name=name,
                source_ref=ensure_evaluation_ref(source_ref),
                target_ref=ensure_evaluation_ref(target_ref),
            )
        )
    return tuple(targets)


def sweep_names(refs: Sequence[str]) -> tuple[str, ...]:
    """Recover the distinct evaluation names present under the evaluation prefix."""
    names = []
    for ref in refs:
        if not ref.startswith(EVALUATION_REF_PREFIX):
            continue
        remainder = ref[len(EVALUATION_REF_PREFIX) :]
        name, _, leaf = remainder.rpartition("/")
        if leaf in ("source", "base") and name and name not in names:
            names.append(name)
    return tuple(names)


def run_teardown(
    targets: Sequence[TeardownTarget],
    *,
    find_pull_requests: DiscoverPullRequestsOperation,
    abandon: AbandonOperation,
    delete_ref: DeleteRefOperation,
    dry_run: bool = False,
) -> TeardownOutcome:
    """Abandon each evaluation draft, then delete the refs that backed it.

    The order is load-bearing: Azure DevOps refuses to delete a ref that is still the
    source of an active pull request, so a delete-first teardown leaves the draft
    orphaned and the ref alive.

    Abandoning is deliberately not deleting. An abandoned pull request keeps its
    published threads and resolves its commits, so reclaiming the branch namespace
    costs none of the evaluation's evidence.

    A ref that is already gone is success, not failure; teardown is re-run by nature.
    """
    abandoned: list[int] = []
    deleted: list[str] = []
    absent: list[str] = []
    for target in targets:
        for pr_id in find_pull_requests(target.source_ref):
            if not dry_run:
                abandon(pr_id)
            abandoned.append(pr_id)
        for ref in (target.source_ref, target.target_ref):
            ensure_evaluation_ref(ref)
            if dry_run or delete_ref(ref):
                deleted.append(ref)
            else:
                absent.append(ref)
    return TeardownOutcome(
        abandoned_pr_ids=tuple(abandoned),
        deleted_refs=tuple(deleted),
        absent_refs=tuple(absent),
        dry_run=dry_run,
    )


def ensure_commit_present(repo: Path, sha: str) -> None:
    """Ensure an exact commit object is available before diffing or pushing it."""
    present = _run_git(["cat-file", "-e", f"{sha}^{{commit}}"], str(repo), check=False)
    if present.ok:
        return
    fetched = _run_git(["fetch", "origin", sha], str(repo), timeout=300.0, check=False)
    if not fetched.ok:
        raise EvaluationError(f"commit {sha} is unavailable from origin: {fetched.stderr.strip()}")
    _run_git(["cat-file", "-e", f"{sha}^{{commit}}"], str(repo))


def resolve_commit(repo: Path, value: str) -> str:
    """Resolve a local commit-ish, fetching the exact value from origin on a miss."""
    resolved = _run_git(
        ["rev-parse", f"{value}^{{commit}}"],
        str(repo),
        check=False,
    )
    if resolved.ok:
        return resolved.stdout.strip()
    fetched = _run_git(["fetch", "origin", value], str(repo), timeout=300.0, check=False)
    if not fetched.ok:
        raise EvaluationError(
            f"commit {value} is unavailable from origin: {fetched.stderr.strip()}"
        )
    return _run_git(["rev-parse", "FETCH_HEAD^{commit}"], str(repo)).stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = _run_git(
        ["merge-base", "--is-ancestor", ancestor, descendant],
        str(repo),
        check=False,
    )
    return result.returncode == 0


def validate_pr_checkpoint(
    repo: Path,
    *,
    checkpoint_sha: str,
    source_tip_sha: str,
    target_sha: str,
) -> None:
    """Prove a checkpoint belongs to the PR source history after its branch point."""
    for sha in (checkpoint_sha, source_tip_sha, target_sha):
        ensure_commit_present(repo, sha)
    if not _is_ancestor(repo, checkpoint_sha, source_tip_sha):
        raise EvaluationError(
            f"checkpoint {checkpoint_sha} is not an ancestor of PR source tip {source_tip_sha}"
        )
    branch_point = _run_git(
        ["merge-base", target_sha, checkpoint_sha],
        str(repo),
    ).stdout.strip()
    if not branch_point or checkpoint_sha == branch_point:
        raise EvaluationError("checkpoint must be strictly after the PR branch point")


def evaluation_refs(name: str) -> tuple[str, str]:
    """Build disposable source/base refs from a caller-visible evaluation name."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip(".-")
    if not slug:
        raise EvaluationError("evaluation name must contain at least one letter or digit")
    stem = f"refs/heads/roundtable/eval/{slug}"
    return f"{stem}/source", f"{stem}/base"


def push_exact_refs(
    revision: EvaluationRevision,
    source_ref: str,
    target_ref: str,
    *,
    repo_path: Path,
) -> None:
    """Push exact commit objects to disposable evaluation refs without force."""
    for sha, ref in ((revision.base_sha, target_ref), (revision.source_sha, source_ref)):
        _run_git(["push", "origin", f"{sha}:{ref}"], str(repo_path), timeout=120.0)


def _draft_description(revision: EvaluationRevision, review: EvaluationReview) -> str:
    lines = [
        "Evaluation-only draft. Do not complete.",
        "",
        "This PR was created after the review completed, so its title and "
        "description were not visible to reviewers.",
        "",
        f"Source commit: `{revision.source_sha}`",
        f"Base commit: `{revision.base_sha}`",
        f"Resolution mode: `{revision.mode}`",
    ]
    if revision.requested_sha:
        lines.append(f"Requested commit: `{revision.requested_sha}`")
    if revision.resolved_pr_id:
        lines.append(f"Resolved source PR: `{revision.resolved_pr_id}`")
    lines.append(f"Roundtable session: `{review.session_dir}`")
    return "\n".join(lines) + "\n"


def _git_api_root(repo: EvaluationRepository) -> str:
    """Base URL for one repository's git REST surface."""
    base_url = build_ado_base_url(repo.host, repo.org)
    project = urllib.parse.quote(repo.project, safe="")
    repository = urllib.parse.quote(repo.name, safe="")
    return f"{base_url}/{project}/_apis/git/repositories/{repository}"


def _ado_json(
    endpoint: str,
    *,
    operation: str,
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 30.0,
) -> dict:
    """Call one ADO REST endpoint with the configured credential.

    Credentials are resolved per call so a long teardown cannot outlive a token it
    captured up front.
    """
    facade = sys.modules.get("roundtable.evaluation")
    auth_resolver = getattr(facade, "ado_auth_header", ado_auth_header)
    auth_header = auth_resolver()
    if not auth_header:
        raise EvaluationError(
            f"{operation} requires ADO credentials. Set ROUNDTABLE_ADO_PAT "
            "(or AZURE_DEVOPS_PAT), or sign in with 'az login'."
        )
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise EvaluationError(f"{operation} failed (HTTP {err.code}): {err.reason}") from err
    except TimeoutError as err:  # pragma: no cover - network
        raise EvaluationError(
            f"{operation} timed out after {timeout:.0f}s. Azure DevOps may still have "
            "applied it; re-run to confirm."
        ) from err


def list_evaluation_refs(
    repo: EvaluationRepository,
    *,
    timeout: float = 30.0,
) -> tuple[str, ...]:
    """List the repository's refs under the disposable evaluation namespace."""
    query = urllib.parse.quote(EVALUATION_REF_PREFIX.removeprefix("refs/"), safe="/")
    endpoint = f"{_git_api_root(repo)}/refs?filter={query}&api-version=7.1"
    body = _ado_json(endpoint, operation="ADO evaluation ref listing", timeout=timeout)
    return tuple(str(entry.get("name") or "") for entry in body.get("value") or ())


def find_evaluation_pull_requests(
    repo: EvaluationRepository,
    source_ref: str,
    *,
    timeout: float = 30.0,
) -> tuple[int, ...]:
    """Find the still-active pull requests a given evaluation ref is the source of.

    Only active ones are returned: abandoning an already-abandoned draft is a no-op
    the caller should not be asked to perform or report.
    """
    query = urllib.parse.quote(source_ref, safe="")
    endpoint = (
        f"{_git_api_root(repo)}/pullrequests"
        f"?searchCriteria.sourceRefName={query}"
        "&searchCriteria.status=active&api-version=7.1"
    )
    body = _ado_json(endpoint, operation="ADO evaluation PR lookup", timeout=timeout)
    return tuple(int(entry.get("pullRequestId") or 0) for entry in body.get("value") or ())


def abandon_pull_request(
    repo: EvaluationRepository,
    pr_id: int,
    *,
    timeout: float = 30.0,
) -> None:
    """Abandon one evaluation draft, leaving its threads and commits readable."""
    endpoint = f"{_git_api_root(repo)}/pullrequests/{pr_id}?api-version=7.1"
    _ado_json(
        endpoint,
        operation=f"ADO abandon of PR {pr_id}",
        method="PATCH",
        payload={"status": "abandoned"},
        timeout=timeout,
    )


def delete_evaluation_ref(
    repo: EvaluationRepository,
    ref: str,
    *,
    timeout: float = REF_DELETE_TIMEOUT,
) -> bool:
    """Delete one evaluation ref. Returns False when it was already gone.

    A timed-out delete may still have been applied: Azure DevOps completes the update
    server-side and the client simply stops waiting for the response. Re-running
    teardown is therefore always safe and is the correct response to a timeout.
    """
    ensure_evaluation_ref(ref)
    query = urllib.parse.quote(ref.removeprefix("refs/"), safe="/")
    listing = _ado_json(
        f"{_git_api_root(repo)}/refs?filter={query}&api-version=7.1",
        operation=f"ADO lookup of {ref}",
    )
    current = next(
        (entry for entry in listing.get("value") or () if entry.get("name") == ref),
        None,
    )
    if current is None:
        return False
    body = _ado_json(
        f"{_git_api_root(repo)}/refs?api-version=7.1",
        operation=f"ADO deletion of {ref}",
        method="POST",
        payload=[
            {
                "name": ref,
                "oldObjectId": current.get("objectId"),
                "newObjectId": "0" * 40,
            }
        ],
        timeout=timeout,
    )
    failures = [entry for entry in body.get("value") or () if not entry.get("success", False)]
    if failures:
        reason = failures[0].get("updateStatus") or "unknown"
        raise EvaluationError(f"ADO deletion of {ref} was rejected: {reason}")
    return True


def create_draft_pull_request(
    revision: EvaluationRevision,
    source_ref: str,
    target_ref: str,
    review: EvaluationReview,
    *,
    timeout: float = 30.0,
) -> DraftPullRequest:
    """Create an evaluation-only draft after the unbiased review completes."""
    repo = revision.repository
    api_root = _git_api_root(repo)
    body = _ado_json(
        f"{api_root}/pullrequests?api-version=7.1",
        operation="ADO draft PR creation",
        method="POST",
        payload={
            "sourceRefName": source_ref,
            "targetRefName": target_ref,
            "title": f"[Roundtable evaluation] {revision.source_label}",
            "description": _draft_description(revision, review),
            "isDraft": True,
        },
        timeout=timeout,
    )
    pr_id = int(body.get("pullRequestId") or 0)
    if pr_id <= 0:
        raise EvaluationError("ADO draft PR response did not include pullRequestId")
    base_url = build_ado_base_url(repo.host, repo.org)
    project = urllib.parse.quote(repo.project, safe="")
    repository = urllib.parse.quote(repo.name, safe="")
    pr_url = f"{base_url}/{project}/_git/{repository}/pullrequest/{pr_id}"
    return DraftPullRequest(pr_id=pr_id, url=pr_url)


def run_evaluation(
    revision: EvaluationRevision,
    *,
    name: str,
    review: ReviewOperation,
    push: PushOperation,
    create: CreateOperation,
    publish: PublishOperation,
) -> EvaluationOutcome:
    """Review first, then create the exact-SHA draft and publish the saved result."""
    source_ref, target_ref = evaluation_refs(name)
    reviewed = review(revision)
    push(revision, source_ref, target_ref)
    draft = create(revision, source_ref, target_ref, reviewed)
    publish_exit_code = publish(reviewed.session_dir, draft.url)
    return EvaluationOutcome(
        revision=revision,
        review=reviewed,
        draft=draft,
        source_ref=source_ref,
        target_ref=target_ref,
        publish_exit_code=publish_exit_code,
    )


__all__ = [
    "EVALUATION_REF_PREFIX",
    "DraftPullRequest",
    "EvaluationError",
    "EvaluationOutcome",
    "EvaluationRepository",
    "EvaluationReview",
    "EvaluationRevision",
    "TeardownOutcome",
    "TeardownTarget",
    "abandon_pull_request",
    "create_draft_pull_request",
    "delete_evaluation_ref",
    "ensure_commit_present",
    "ensure_evaluation_ref",
    "evaluation_refs",
    "find_evaluation_pull_requests",
    "list_evaluation_refs",
    "push_exact_refs",
    "resolve_commit",
    "run_evaluation",
    "run_teardown",
    "sweep_names",
    "teardown_targets",
    "validate_pr_checkpoint",
]
