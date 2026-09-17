"""Azure DevOps publication, anchoring, and REST contracts."""

# ruff: noqa: I001

# The REST primitives must exist before publication modules import the input
# facade; PR input loading uses the same authentication primitives.
from roundtable.ado_client import (
    ADO_RESOURCE_ID,
    ado_auth_header,
    ado_bearer_token,
    build_ado_base_url,
)
from .anchor import AnchorDecision, normalize_repo_path
from .adoption import (
    AzureDevOpsAdoptionCollector,
    adoption_record_url,
    collect_to as collect_adoption_to,
)
from .comment_format import summary_stable_hash
from .markdown import clean_markdown
from .publish import (
    GroundingUnit,
    PublishableFinding,
    compute_stable_hash,
    locations_of,
    watermark_hashed,
)
from .publish_flow import PublishOptions
from .review_record import build_review_record, record_review, retry_record
from .unpublish_flow import UnpublishOptions
from .evaluation import (
    EVALUATION_REF_PREFIX,
    DraftPullRequest,
    EvaluationError,
    EvaluationOutcome,
    EvaluationRepository,
    EvaluationReview,
    EvaluationRevision,
    REF_DELETE_TIMEOUT,
    TeardownOutcome,
    TeardownTarget,
    abandon_pull_request,
    create_draft_pull_request,
    delete_evaluation_ref,
    ensure_commit_present,
    ensure_evaluation_ref,
    evaluation_refs,
    find_evaluation_pull_requests,
    list_evaluation_refs,
    push_exact_refs,
    resolve_commit,
    run_evaluation,
    run_teardown,
    sweep_names,
    teardown_targets,
    validate_pr_checkpoint,
)

__all__ = [
    "ADO_RESOURCE_ID",
    "EVALUATION_REF_PREFIX",
    "REF_DELETE_TIMEOUT",
    "AnchorDecision",
    "AzureDevOpsAdoptionCollector",
    "DraftPullRequest",
    "EvaluationError",
    "EvaluationOutcome",
    "EvaluationRepository",
    "EvaluationReview",
    "EvaluationRevision",
    "GroundingUnit",
    "PublishOptions",
    "PublishableFinding",
    "TeardownOutcome",
    "TeardownTarget",
    "UnpublishOptions",
    "abandon_pull_request",
    "ado_auth_header",
    "ado_bearer_token",
    "adoption_record_url",
    "build_ado_base_url",
    "build_review_record",
    "clean_markdown",
    "collect_adoption_to",
    "compute_stable_hash",
    "create_draft_pull_request",
    "delete_evaluation_ref",
    "ensure_commit_present",
    "ensure_evaluation_ref",
    "evaluation_refs",
    "find_evaluation_pull_requests",
    "list_evaluation_refs",
    "locations_of",
    "normalize_repo_path",
    "push_exact_refs",
    "record_review",
    "resolve_commit",
    "retry_record",
    "run_evaluation",
    "run_teardown",
    "summary_stable_hash",
    "sweep_names",
    "teardown_targets",
    "validate_pr_checkpoint",
    "watermark_hashed",
]
