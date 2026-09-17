"""Roundtable: a configurable multi-agent orchestration framework.

It executes validated agent graphs with deterministic orchestration, inspectable
artifacts, and replay. Buddies and InspectorX are shipped code-review configurations.
Copilot is the production LLM backend; Azure DevOps integrations are available for
review publication, adoption, and evaluation.

Common commands:
  roundtable run ...                run any configuration with explicit source inputs
  roundtable doctor                 check setup and authenticated runtime capabilities
  roundtable review <repo>          review a branch and save the verdict + report
  roundtable review <repo> --pr 42  review an Azure DevOps pull request
  roundtable view <session>         show a saved review's verdict and findings
  roundtable report <session>       open an interactive HTML report of a saved review
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from roundtable.ado import PublishOptions, UnpublishOptions
from roundtable.bundle import (
    ENV_VAR,
    ConfigRootError,
    bundle_root,
    migrate_legacy_home_dir,
    resolve_bundle,
    set_config_root,
    shipped_bundles,
)
from roundtable.context import (
    HintTooLarge,
    SessionHeaderInputs,
    build_session_header,
    diff_cap_stats,
    parse_diff_stats,
    render_ado_context_for_servers,
    stage_hint,
)
from roundtable.decision import (
    EXIT_ABORTED,
    EXIT_BAD_ARGS,
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_FINDINGS,
)
from roundtable.evaluation import (
    EvaluationError,
    EvaluationRepository,
    EvaluationReview,
    EvaluationRevision,
    abandon_pull_request,
    create_draft_pull_request,
    delete_evaluation_ref,
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
from roundtable.graph import Configuration, get_configuration, graph_config_sha
from roundtable.inputs import (
    GitError,
    Mode,
    ReplayError,
    ReplaySession,
    WorkspaceError,
    WorkspaceRequest,
    ado_clone_url,
    build_review_workspace,
    capture_replay_context,
    load_replay_session,
    materialize_replay_access,
    parse_ado_remote_url,
    reconcile_orphans,
    restore_replay_session,
)
from roundtable.mcp import (
    READY,
    McpBuildContext,
    ado_servers,
    prune_servers,
    resolve_mcp_config,
    unique_servers,
    unreachable,
    warm_and_probe,
)
from roundtable.records import write_repo_index
from roundtable.review import (
    OverlayKey,
    make_session_id,
    repo_slug,
    run_review,
)
from roundtable.runtime import (
    graph_custom_agents,
    load_mcp_needs_map,
    validate_agents,
)
from roundtable.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_ATTEMPTS,
    UNSET_FALLBACK,
    ConfigError,
    ReviewConfig,
    build_review_config,
    get_settings,
    render_params,
    settings_params,
    validate_update_source,
    with_legacy_fallback,
)

from . import __version__
from .context_boundary import check_context_import_boundary
from .inputs import (
    PrMetadata,
    PrReference,
    ReviewContextOptions,
    detect_repos,
    determine_base_branch,
    fetch_pr_by_merge_commit,
    fetch_pr_metadata,
    gather_review_context,
    parse_pr_reference,
    resolve_ado_identities,
    resolve_target_ref,
)
from .persistence_boundary import check_persistence_import_boundary
from .rebrand_guard import check_rebrand_guard
from .review_skill import (
    default_destination as review_skill_destination,
)
from .review_skill import (
    install as install_review_skill,
)
from .review_skill import (
    status as review_skill_status,
)
from .review_skill import (
    uninstall as uninstall_review_skill,
)
from .validation_boundary import check_validation_import_boundary

if TYPE_CHECKING:
    from roundtable.capabilities import RuntimeCapabilities
    from roundtable.delivery import Publisher


class _BaseBranchUnresolved(ValueError):
    """The diff base branch could not be resolved in the local repo, so the
    review would be empty (a false-CLEAN). Mapped to ``EXIT_BAD_ARGS`` at the
    command boundary — the pre-flight hard-fails instead of silently degrading
    to an empty review.
    """


def _resolve_verify_base(repo_path: Path, explicit_base: str | None, log_prefix: str) -> str:
    """Resolve the diff base branch (auto-detect when ``--base-branch`` is omitted)
    and verify it actually resolves in ``repo_path``.

    Raises :class:`_BaseBranchUnresolved` instead of degrading to an empty review
    when the base cannot be found — the local-mode pre-flight gate.
    """
    base = explicit_base
    if not base:
        base = determine_base_branch(str(repo_path))
        print(f"{log_prefix} --base-branch omitted; auto-detected base = {base}", file=sys.stderr)
    try:
        resolve_target_ref(str(repo_path), base)
    except ValueError as err:
        raise _BaseBranchUnresolved(
            f"Base branch '{base}' was not found in this repository, so there is "
            f"nothing to diff against and the review would be empty. Specify the "
            f"correct base with --base-branch <name> (e.g. --base-branch master)."
        ) from err
    return base


def _cmd_inputs(args: argparse.Namespace) -> int:
    """Phase 1: build + print the review inputs (git context) for a repo.

    Local mode (default): diff the current checkout against the resolved base
    branch. PR mode (``--pr``): resolve commit SHAs from ADO REST and diff the
    SHA range. The formatted git context goes to stdout; diagnostics to stderr.
    """
    repo_path = Path(args.repo).resolve()
    repos = detect_repos(str(repo_path))
    if not repos:
        print(f"[inputs] not a git repository: {repo_path}", file=sys.stderr)
        return 2
    repo = repos[0]
    print(
        f"[inputs] repo={repo.name} branch={repo.branch} remote={repo.remote_url}", file=sys.stderr
    )

    if args.pr:
        settings = get_settings().workspace
        workspace = None
        try:
            pr = parse_pr_reference(args.pr, repo.remote_url)
            metadata = fetch_pr_metadata(pr)
            worktree_dir = Path(tempfile.mkdtemp(prefix="roundtable-inputs-")) / "worktree"
            workspace = build_review_workspace(
                WorkspaceRequest(
                    mode="pr",
                    repo_path=str(repo_path),
                    remote_url=ado_clone_url(pr),
                    source_sha=metadata.source_commit_sha,
                    base_sha=metadata.target_commit_sha,
                    target_ref=metadata.target_branch,
                ),
                settings=settings,
                worktree_dir=worktree_dir,
                contextual_dirs=[str(repo_path)],
            )
            result = gather_review_context(
                ReviewContextOptions(
                    worktree_path=str(workspace.path),
                    repo_name=repo.name,
                    target_branch=metadata.target_branch or "main",
                    source_branch=_reviewed_branch(repo, metadata),
                    source_sha=metadata.source_commit_sha,
                    mode=cast(Mode, workspace.mode),
                )
            )
        except Exception as err:
            print(f"[inputs] PR mode failed: {err}", file=sys.stderr)
            return 2
        finally:
            if workspace is not None:
                workspace.cleanup()
        assert workspace is not None  # set in the try; the except path returned
        md = result.metadata
        print(
            f"[inputs] pr=#{pr.pr_id} {metadata.target_branch}...{metadata.source_branch} "
            f"files={md.changed_file_count} snapshot={md.snapshot_sha[:8]} mode={workspace.mode}",
            file=sys.stderr,
        )
        print(result.git_context)
        return 0 if result.changed_files else 1

    try:
        base_branch = _resolve_verify_base(repo_path, args.base_branch, "[inputs]")
    except _BaseBranchUnresolved as err:
        print(f"[inputs] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    result = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo_path),
            repo_name=repo.name,
            target_branch=base_branch,
            source_branch=_reviewed_branch(repo, None),
        )
    )
    md = result.metadata
    print(
        f"[inputs] diff={md.diff_command} files={md.changed_file_count} "
        f"lines={md.diff_line_count} snapshot={md.snapshot_sha[:8]} "
        f"base={(md.base_sha or 'null')[:8]}",
        file=sys.stderr,
    )
    print(result.git_context)
    return 0 if result.changed_files else 1


def _resolve_hint_args(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve the ``--hint`` / ``--hint-path`` flags for the Author Context block.

    The inline hint is passed through as-is (the header caps it). The file/dir hint
    is a pointer, not inlined: its path is validated so a typo surfaces as a warning
    rather than a silent no-op, but a missing path is non-fatal — the review still
    runs. The path is resolved to an **absolute** path (against the CLI cwd) so it is
    unambiguous regardless of where an agent subprocess runs. Returns
    ``(hint, hint_src)`` — the second is the validated absolute *source* path (a file
    or directory) to be staged, with empty values normalized to ``None``.
    """
    hint = (getattr(args, "hint", None) or "").strip() or None
    raw_hint_path = (getattr(args, "hint_path", None) or "").strip() or None
    hint_src: str | None = None
    if raw_hint_path:
        resolved = Path(raw_hint_path).expanduser().resolve()
        if resolved.exists():
            hint_src = str(resolved)
        else:
            print(
                f"[review] --hint-path not found: {raw_hint_path} (continuing without it)",
                file=sys.stderr,
            )
    return hint, hint_src


def _corpus_payloads(diff: str, git_history: str = "") -> dict[str, str]:
    """Map the gathered corpus onto the ``kind: source`` node keys the graph declares.

    The ``ReviewDiff`` source receives the diff; ``GitHistory`` receives the per-file
    history (only when non-empty — the ``--pr`` path gathers none). These source nodes
    run as graph roots and emit the payload as their outcome, from which every consumer
    reads the corpus via its own source edges (replacing the pre-graph
    ``InjectionInputs``). The CLI is Roundtable-specific, so it names its two source
    nodes directly; a consumer's edge to them is validated by ``doctor``.
    """
    payloads: dict[str, str] = {"ReviewDiff": diff}
    if git_history:
        payloads["GitHistory"] = git_history
    return payloads


def _build_review_context(
    args: argparse.Namespace,
    repo,
    workspace,
    pr: PrReference | None = None,
    metadata: PrMetadata | None = None,
    *,
    hint: str | None = None,
    hint_path: str | None = None,
) -> tuple[str, str, McpBuildContext, dict[str, str], list[str], list, dict[str, Any]]:
    """Build the review context (diff) + session header + MCP build context
    + corpus source payloads + resolved ADO identities.

    The diff is gathered from ``workspace.path`` — a directory guaranteed to be at
    the reviewed revision (the working tree in place for a local review; a detached
    worktree at the PR source SHA for ``--pr`` / URL). Both modes now flow through
    the single :func:`gather_review_context` three-dot gatherer, so ``--pr`` no
    longer diverges into the old REST/SHA-range path.

    The session header (``## Change Under Review``) is a per-session constant
    prepended verbatim to every agent context. The per-agent ADO Tool Bindings
    block (Identity + Tool Bindings) is NOT in the header — it is rendered PER
    AGENT for agents that opt into an ADO server (see ``_cmd_review``
    ``ado_context_by_key``), so the returned ``identities`` are threaded out for
    that per-agent render.

    ADO is a uniform opt-in MCP server like any other: when an ADO
    identity resolves for the repo/PR, the returned :class:`McpBuildContext`
    carries ``ado_org`` so the registry can build the ``ado-*`` server configs
    (only for agents that opt in). A non-ADO repo (no identity) silently runs
    without the ADO servers/sections — ``ado_org`` is ``None`` and
    ``identities`` is empty.
    """
    source_branch = _reviewed_branch(repo, metadata)
    if args.pr:
        assert pr is not None and metadata is not None  # guaranteed in PR mode
        target_branch = metadata.target_branch or "main"
        result = gather_review_context(
            ReviewContextOptions(
                worktree_path=str(workspace.path),
                repo_name=repo.name,
                target_branch=target_branch,
                source_branch=source_branch,
                source_sha=metadata.source_commit_sha,
                mode=workspace.mode,
            )
        )
        identities = resolve_ado_identities(pr=pr, remote_url=repo.remote_url)
        ado_active = bool(identities)
        header = build_session_header(
            SessionHeaderInputs(
                target_branch=target_branch,
                source_branch=source_branch,
                source_sha=metadata.source_commit_sha,
                diff_stats=parse_diff_stats(result.git_context),
                pr_id=pr.pr_id,
                pr_title=metadata.title,
                files_body_truncated=diff_cap_stats(result.git_context).truncated_files,
                workspace_path=str(workspace.path),
                review_mode=workspace.mode,
                pr_description=metadata.description,
                hint=hint,
                hint_path=hint_path,
            ),
        )
        mcp_ctx = McpBuildContext(ado_org=identities[0].org if ado_active else None)
        subject = _subject(
            mode="pr",
            repo=repo,
            pr_id=pr.pr_id,
            pr_title=metadata.title,
            target_branch=target_branch,
            source_branch=source_branch,
            source_sha=metadata.source_commit_sha,
            base_sha=metadata.target_commit_sha,
        )
        return (
            result.git_context,
            header,
            mcp_ctx,
            # --pr path: the unified gatherer now yields per-file history too; the
            # DPS base scan remains a scheduled deterministic node.
            _corpus_payloads(
                result.git_context,
                getattr(result, "git_history", "") or "",
            ),
            result.changed_files,
            list(identities) if ado_active else [],
            subject,
        )

    base_branch = _resolve_verify_base(workspace.path, args.base_branch, "[review]")
    result = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(workspace.path),
            repo_name=repo.name,
            target_branch=base_branch,
            source_branch=source_branch,
            mode=workspace.mode,
        )
    )
    identities = resolve_ado_identities(repo_path=str(workspace.path), remote_url=repo.remote_url)
    ado_active = bool(identities)
    header = build_session_header(
        SessionHeaderInputs(
            target_branch=base_branch,
            source_branch=source_branch,
            source_sha=result.metadata.snapshot_sha,
            diff_stats=parse_diff_stats(result.git_context),
            pr_id=None,
            pr_title=None,
            files_body_truncated=diff_cap_stats(result.git_context).truncated_files,
            workspace_path=str(workspace.path),
            review_mode=workspace.mode,
            pr_description=None,
            hint=hint,
            hint_path=hint_path,
        ),
    )
    mcp_ctx = McpBuildContext(ado_org=identities[0].org if ado_active else None)
    source_payloads = _corpus_payloads(
        result.git_context,
        getattr(result, "git_history", "") or "",
    )
    subject = _subject(
        mode="branch",
        repo=repo,
        pr_id=None,
        pr_title=None,
        target_branch=base_branch,
        source_branch=source_branch,
        source_sha=result.metadata.snapshot_sha,
        base_sha=result.metadata.base_sha,
    )
    return (
        result.git_context,
        header,
        mcp_ctx,
        source_payloads,
        result.changed_files,
        list(identities) if ado_active else [],
        subject,
    )


def _subject(
    *,
    mode: str,
    repo,
    pr_id: object,
    pr_title: str | None,
    target_branch: str | None,
    source_branch: str | None,
    source_sha: str | None,
    base_sha: str | None,
) -> dict[str, Any]:
    """Build the ``trace.json`` ``subject`` block — what this review targeted.

    ``None``-valued fields are dropped so a branch review (no PR) stays slim and the
    derived index only carries meaningful columns. ``base_sha`` is the diff's base
    endpoint (branch merge-base / PR target commit) — with ``source_sha`` it makes the
    exact ``git diff base..source`` reproducible.
    """
    fields = {
        OverlayKey.SUBJECT_MODE: mode,
        OverlayKey.SUBJECT_REPO: repo.name,
        OverlayKey.SUBJECT_REMOTE_URL: repo.remote_url,
        OverlayKey.SUBJECT_PR_ID: pr_id,
        OverlayKey.SUBJECT_PR_TITLE: pr_title,
        OverlayKey.SUBJECT_TARGET_BRANCH: target_branch,
        OverlayKey.SUBJECT_SOURCE_BRANCH: source_branch,
        OverlayKey.SUBJECT_SOURCE_SHA: source_sha,
        OverlayKey.SUBJECT_BASE_SHA: base_sha,
    }
    return {k: v for k, v in fields.items() if v is not None}


def _resolve_backend_name(*, backend: str | None, simulate: bool) -> str:
    if backend is not None and simulate:
        raise ValueError("--backend cannot be combined with --simulate")
    return "mock" if simulate else backend or "copilot"


def _diff_stat(diff: str, changed_files: list[str]) -> dict[str, Any]:
    """Build the ``trace.json`` ``diffStat`` block — the change-set fingerprint.

    ``filesChanged``/``insertions``/``deletions`` come from the raw diff;
    ``changedFiles`` is the explicit path list so a review can be tied to exactly
    which files it saw.
    """
    stats = parse_diff_stats(diff)
    return {
        "filesChanged": stats.files_changed,
        "insertions": stats.insertions,
        "deletions": stats.deletions,
        "changedFiles": list(changed_files),
    }


def _git_context(
    workspace,
    *,
    add_dirs: list[str],
    cwd: str | None,
    changed_files: list[str],
) -> dict[str, Any]:
    """Build the standalone ``git-context.json`` block — *where* the review read.

    Records how the workspace was resolved (mode + SHAs), the exact paths the
    agents were granted (``add_dirs`` / ``cwd``), and the discovery decision (which
    local clone matched, or a clone), so a reviewer can audit that the agents read
    the reviewed revision without re-deriving it from process state.
    """
    disc = getattr(workspace, "discovery", None)
    discovery = (
        {
            "source": disc.source,
            "remoteKey": disc.remote_key,
            "path": str(disc.path) if disc.path is not None else None,
            "candidates": list(disc.candidates),
            "reason": disc.reason,
        }
        if disc is not None
        else None
    )
    return {
        "mode": workspace.mode,
        "snapshotSha": workspace.source_sha,
        "baseSha": workspace.base_sha,
        "workspacePath": str(workspace.path),
        "clonePath": (
            str(workspace.clone_path) if getattr(workspace, "clone_path", None) else None
        ),
        "addDirs": list(add_dirs),
        "cwd": cwd,
        "changedFileCount": len(changed_files),
        "discovery": discovery,
    }


_UNKNOWN_HOST = "unknown"


def _host_name() -> str:
    """This machine's network name, portably.

    ``platform.node()`` covers Windows (``COMPUTERNAME``), macOS and Linux (the
    ``uname`` node), but is documented to return ``""`` when it cannot be
    determined — so fall back rather than record an empty name.
    """
    if name := platform.node():
        return name
    try:
        return socket.gethostname() or _UNKNOWN_HOST
    except OSError:
        return _UNKNOWN_HOST


def _host() -> dict[str, str]:
    """Which machine ran the review — its artifacts path means nothing elsewhere."""
    return {"name": _host_name(), "os": platform.system() or _UNKNOWN_HOST}


def _provenance(
    args: argparse.Namespace, effective: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build the ``trace.json`` ``provenance`` block — how the review ran.

    Ties a verdict to the exact reviewer build (``toolVersion``) + agent-system
    config (``graphConfigSha``) + the determinism-affecting CLI flags
    (``invocation``), so a result is reproducible and comparable. ``host`` records
    where it ran, because the saved artifacts live on that machine only.
    ``effectiveConfig``
    (when supplied) records every run parameter's resolved value AND the layer that
    won it (``flag`` / ``env`` / ``roundtable.yaml`` / ``default``), so the config
    is fully self-describing without re-deriving the precedence.
    """
    block: dict[str, Any] = {
        "toolName": "roundtable",
        "toolVersion": __version__,
        "graphConfigSha": graph_config_sha(),
        "host": _host(),
        "invocation": {
            "baseBranch": args.base_branch,
            "pr": args.pr,
            "maxAttempts": getattr(args, "max_attempts", None),
            "concurrency": getattr(args, "concurrency", None),
            "sessionReuse": getattr(args, "session_reuse", None),
            "simulate": getattr(args, "simulate", None),
            "hint": bool(getattr(args, "hint", None)),
            "hintPath": getattr(args, "hint_path", None) or None,
        },
    }
    if effective is not None:
        block["effectiveConfig"] = effective
        # The max_attempts flag defaults to None on the Namespace now (so an
        # explicit flag is detectable); surface its *resolved* value here.
        by_name = {e["name"]: e["value"] for e in effective}
        block["invocation"]["maxAttempts"] = by_name.get("max_attempts")
        block["invocation"]["concurrency"] = by_name.get("concurrency")
    return block


@dataclass(frozen=True)
class _ReviewTarget:
    """A resolved review target: the workspace at the reviewed revision plus the
    repo identity, PR coordinates (``--pr`` only), and the session/artifact paths."""

    workspace: Any
    repo: Any
    pr: Any
    metadata: Any
    label: str
    base_dir: Path
    session_id: str
    replay_session: ReplaySession | None = None

    @property
    def branch(self) -> str:
        """The branch under review — see :func:`_reviewed_branch`."""
        if self.replay_session is not None:
            return self.replay_session.context.revision.source_branch
        return _reviewed_branch(self.repo, self.metadata)


def _resolve_replay_target(
    input_from: str,
    artifacts_root: Path,
    configuration: Configuration,
) -> _ReviewTarget:
    saved = load_replay_session(input_from)
    context = saved.context
    label = f"replay-{saved.session_dir.name}"
    session_id = make_session_id(label)
    base_dir = artifacts_root / repo_slug(context.repository.name)
    restored = restore_replay_session(
        input_from,
        settings=get_settings().workspace,
        worktree_dir=base_dir / session_id / "worktree",
    )
    if saved.graph_config_sha is not None:
        current = graph_config_sha(configuration.root)
        if current != saved.graph_config_sha:
            print(
                "[review] WARNING replay config drift: "
                f"recorded={saved.graph_config_sha}, current={current}",
                file=sys.stderr,
            )
    repo = SimpleNamespace(
        name=context.repository.name,
        branch=context.revision.source_branch,
        remote_url=context.repository.fetch_url,
    )
    return _ReviewTarget(
        workspace=restored.workspace,
        repo=repo,
        pr=None,
        metadata=None,
        label=label,
        base_dir=base_dir,
        session_id=session_id,
        replay_session=restored.session,
    )


def _reviewed_branch(repo, metadata) -> str:
    """The branch under review: the PR's source branch, else the local checkout's.

    The one derivation every consumer reads — the banner, the review context, the
    trace subject and the publish anchor. A ``--pr`` workspace is a **detached**
    worktree at the source SHA, so the repo detected from it reports no branch at
    all; reading ``repo.branch`` there yields ``None`` for every PR review.
    """
    if metadata is not None:
        return metadata.source_branch
    return repo.branch or "HEAD"


def _pr_repo_fallback(pr, metadata):
    """A minimal repo descriptor for the rare case a freshly materialized worktree
    is not detected as a repo (defensive — a worktree normally detects cleanly).

    ``branch`` is ``None`` to match what detection reports for a PR worktree: it is
    a detached checkout at the source SHA and has no branch of its own. The branch
    under review comes from :func:`_reviewed_branch`, never from here.
    """
    return SimpleNamespace(
        name=pr.repo_name,
        branch=None,
        remote_url=ado_clone_url(pr),
    )


def _resolve_review_target(args: argparse.Namespace, artifacts_root: Path) -> _ReviewTarget:
    """Detect the repo, resolve the PR (``--pr``), and materialize a workspace at
    the reviewed revision.

    * ``--pr`` / URL: fetch the PR metadata (source/target SHAs), then resolve a
      detached worktree at the source SHA — discovering a local clone by remote
      identity or cloning into the bounded cache on a miss. A local checkout is not
      required (a bare id still needs one for its remote; a full URL bootstraps
      without one).
    * local: review the working tree in place (defaults to the current directory).

    The session id / artifact ``base_dir`` are minted here so the per-session
    worktree lands under ``<artifacts>/<repo-slug>/<session_id>/worktree/``.
    """
    if args.repo is not None:
        repo_path = Path(args.repo).resolve()
    elif not args.pr:
        repo_path = Path.cwd()  # local mode defaults to the current directory
    else:
        repo_path = None  # PR-by-URL bootstraps its own clone; no checkout needed
    local_repos = detect_repos(str(repo_path)) if repo_path is not None else []
    settings = get_settings().workspace
    reconcile_orphans(settings)

    if args.pr:
        remote_hint = local_repos[0].remote_url if local_repos else None
        pr = parse_pr_reference(args.pr, remote_hint)
        metadata = fetch_pr_metadata(pr)
        label = f"pr-{pr.pr_id}-{metadata.source_branch}"
        session_id = make_session_id(label)
        base_dir = artifacts_root / repo_slug(pr.repo_name)
        workspace = build_review_workspace(
            WorkspaceRequest(
                mode="pr",
                repo_path=str(repo_path) if local_repos else None,
                remote_url=ado_clone_url(pr),
                source_sha=metadata.source_commit_sha,
                base_sha=metadata.target_commit_sha,
                target_ref=metadata.target_branch,
            ),
            settings=settings,
            worktree_dir=base_dir / session_id / "worktree",
            contextual_dirs=[str(repo_path)] if local_repos else (),
        )
        detected = detect_repos(str(workspace.path))
        repo = detected[0] if detected else _pr_repo_fallback(pr, metadata)
        return _ReviewTarget(workspace, repo, pr, metadata, label, base_dir, session_id)

    if not local_repos:
        raise WorkspaceError(f"not a git repository: {repo_path}")
    repo = local_repos[0]
    label = repo.branch or repo.name
    session_id = make_session_id(label)
    base_dir = artifacts_root / repo_slug(repo.name)
    workspace = build_review_workspace(
        WorkspaceRequest(mode="local", repo_path=str(repo_path)),
        settings=settings,
        worktree_dir=base_dir / session_id / "worktree",
    )
    return _ReviewTarget(workspace, repo, None, None, label, base_dir, session_id)


def _preflight_graph(
    configuration: Configuration | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
) -> str | None:
    """Graph errors that would make a review fail slowly, or ``None`` if clean.

    Run before any clone or LLM call: a tool name the runtime does not serve is
    ignored silently, so without this the agent runs stripped of a capability and
    the loss only shows up as a bad review hours later.
    """
    config = configuration or get_configuration()
    report = validate_agents(
        bundle_root(),
        config=config,
        runtime_capabilities=runtime_capabilities,
    )
    return None if report.ok else report.failure_text


def _resolve_runtime_capabilities_or_report(command: str) -> RuntimeCapabilities | None:
    from roundtable.capabilities import resolve_runtime_capabilities

    try:
        return resolve_runtime_capabilities()
    except Exception as err:
        print(
            f"[{command}] authenticated runtime capability discovery failed "
            f"— refusing to continue: {err}",
            file=sys.stderr,
        )
        return None


def _resolve_runtime_preflight_or_report(
    command: str,
    configuration: Configuration,
) -> RuntimeCapabilities | None:
    capabilities = _resolve_runtime_capabilities_or_report(command)
    if capabilities is None:
        return None
    failures = _preflight_graph(configuration, capabilities)
    if failures is None:
        return capabilities
    print(f"[{command}] agent graph is invalid — refusing to run:", file=sys.stderr)
    print(failures, file=sys.stderr)
    return None


def _cmd_review(args: argparse.Namespace) -> int:
    """Phase 5: run the full review pipeline → verdict → persisted artifacts.

    ``--dry-run`` exercises everything except the live LLM graph (repo detection,
    context build, agent installation) so the wiring is verifiable without
    spending tokens. The live path runs the graph and persists trace.json /
    verdict.md, returning the canonical verdict→exit code.

    A review workspace (a directory at the reviewed revision) is resolved up front
    and torn down in ``finally`` so a ``--pr`` clone-worktree never leaks.
    """
    input_from = getattr(args, "input_from", None)
    conflicts = [
        flag
        for flag, value in (
            ("--pr", getattr(args, "pr", None)),
            ("repo", getattr(args, "repo", None)),
            ("--base-branch", getattr(args, "base_branch", None)),
        )
        if input_from and value is not None
    ]
    if conflicts:
        print(
            f"[review] --input-from cannot be combined with {', '.join(conflicts)}",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS
    try:
        backend_name = _resolve_backend_name(
            backend=getattr(args, "backend", None),
            simulate=getattr(args, "simulate", False),
        )
    except ValueError as err:
        print(f"[review] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    args._backend_name = backend_name
    configuration = get_configuration()
    try:
        _require_review_domain_values(configuration, feature="roundtable review")
    except ValueError as err:
        print(f"[review] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    live_preflight = backend_name == "copilot" and not getattr(args, "dry_run", False)
    runtime_capabilities = getattr(args, "_runtime_capabilities", None)
    if live_preflight:
        if runtime_capabilities is None:
            runtime_capabilities = _resolve_runtime_preflight_or_report("review", configuration)
        if runtime_capabilities is None:
            return EXIT_ERROR
    failures = None if live_preflight else _preflight_graph(configuration)
    if failures is not None:
        print("[review] agent graph is invalid — refusing to run:", file=sys.stderr)
        print(failures, file=sys.stderr)
        return EXIT_ERROR
    # One place resolves the whole flag>env>file>default precedence for this run
    # (config.effective); the resolved values drive behaviour AND are printed +
    # recorded, so what ran is never a mystery. Resolved here, before the workspace,
    # because the artifacts root it yields is where the session lands.
    review_cfg = build_review_config(get_settings(), args)
    print(render_params(review_cfg.params, title="[review] effective config"), file=sys.stderr)
    workspace = None
    try:
        try:
            target = (
                _resolve_replay_target(input_from, review_cfg.artifacts_root, configuration)
                if input_from
                else _resolve_review_target(args, review_cfg.artifacts_root)
            )
        except ReplayError as err:
            print(f"[review] invalid replay session: {err}", file=sys.stderr)
            return EXIT_BAD_ARGS
        except WorkspaceError as err:
            print(f"[review] {err}", file=sys.stderr)
            return EXIT_ERROR
        except Exception as err:
            print(f"[review] failed to resolve review workspace: {err}", file=sys.stderr)
            return EXIT_ERROR
        workspace = target.workspace
        return _execute_review(args, target, review_cfg, configuration)
    finally:
        if workspace is not None:
            workspace.cleanup()


def _execute_review(
    args: argparse.Namespace,
    target: _ReviewTarget,
    review_cfg: ReviewConfig,
    configuration: Configuration,
) -> int:
    """Build context, run the graph, persist, and optionally publish for a resolved
    :class:`_ReviewTarget`. Workspace teardown is owned by :func:`_cmd_review`."""
    repo = target.repo
    workspace = cast(Any, target.workspace)
    if workspace is None:
        print("[review] resolved target has no workspace", file=sys.stderr)
        return EXIT_ERROR
    base_dir = target.base_dir
    session_id = target.session_id
    label = target.label

    # Resolved once here (the single eval): the header needs the inline hint, and
    # both the header pointer and the add_dirs grant below need the staged path.
    hint, hint_src = _resolve_hint_args(args)

    # Agents run with cwd=workspace.path and read access to it. A --hint-path lives
    # outside that worktree, so instead of widening the grant to its real parent
    # (which would expose unrelated siblings), copy it into a session-scoped staging
    # dir holding ONLY the hint and grant that one dir. The header points at the
    # staged copy — the path agents can actually read.
    add_dirs = [str(workspace.path)] if workspace is not None else []
    hint_pointer: str | None = None
    hint_grant_dir: str | None = None
    if hint_src:
        try:
            staged = stage_hint(Path(hint_src), base_dir / session_id / "hints")
        except HintTooLarge as err:
            print(f"[review] --hint-path too large, continuing without it: {err}", file=sys.stderr)
        except OSError as err:
            print(
                f"[review] --hint-path could not be staged ({err}); continuing without it",
                file=sys.stderr,
            )
        else:
            hint_pointer = str(staged.pointer)
            hint_grant_dir = str(staged.grant_dir)
            add_dirs.append(hint_grant_dir)

    try:
        if target.replay_session is not None:
            replay = target.replay_session
            replay_context = replay.context
            source_payloads = replay.source_payloads
            context = source_payloads.get("ReviewDiff", "")
            session_header, replay_grants = materialize_replay_access(
                replay,
                Path(cast(Any, workspace).path),
            )
            add_dirs.extend(replay_grants)
            identities = list(replay_context.ado_identities)
            mcp_ctx = McpBuildContext(ado_org=identities[0].org if identities else None)
            changed_files = list(replay_context.changed_files)
            revision = replay_context.revision
            subject = {
                "mode": revision.mode,
                "repo": replay_context.repository.name,
                "remoteUrl": replay_context.repository.fetch_url,
                "targetBranch": revision.base_branch,
                "sourceBranch": revision.source_branch,
                "sourceSha": revision.source_sha,
                "baseSha": revision.base_sha,
                "inputFrom": str(replay.session_dir),
            }
        else:
            (
                context,
                session_header,
                mcp_ctx,
                source_payloads,
                changed_files,
                identities,
                subject,
            ) = _build_review_context(
                args,
                repo,
                workspace,
                target.pr,
                target.metadata,
                hint=hint,
                hint_path=hint_pointer,
            )
            replay_context = capture_replay_context(
                repo_name=repo.name,
                repo_path=Path(cast(Any, workspace).path),
                remote_url=repo.remote_url,
                mode=str(subject["mode"]),
                source_sha=str(subject["sourceSha"]),
                base_sha=str(subject["baseSha"]),
                source_branch=str(subject["sourceBranch"]),
                base_branch=str(subject["targetBranch"]),
                ado_identities=list(identities),
                changed_files=changed_files,
                session_header=session_header,
                session_dir=base_dir / session_id,
                hint_path=hint_pointer,
                hint_grant_dir=hint_grant_dir,
                capture_overlay=target.pr is None,
            )
    except _BaseBranchUnresolved as err:
        print(f"[review] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    except Exception as err:
        print(f"[review] failed to build review context: {err}", file=sys.stderr)
        return EXIT_ERROR

    # The SDK backend receives every agent's identity + composed prompt in memory
    # (``graph_custom_agents``) — there is no on-disk install and no per-session
    # Copilot config home. ``base_dir``/``session_id`` were minted up front by
    # ``_resolve_review_target`` so the worktree and persisted artifacts share one
    # session directory.
    custom_agents = graph_custom_agents(bundle_root(), configuration.entries)
    agent_count = len(custom_agents)
    # MCP provisioning is exact and per-agent: the graph names each server the
    # custom agent receives; no ambient/default server set is merged in.
    mcp_needs = load_mcp_needs_map(bundle_root(), configuration.entries)
    mcp_servers_by_key = {
        key: resolve_mcp_config(servers, mcp_ctx) for key, servers in mcp_needs.items()
    }
    # Pre-flight MCP warm-up + health probe (runtime.mcp_prewarm). Spawning each
    # resolved server ONCE before the DAG primes the npx package cache + auth token
    # so every agent session starts it WARM (its tools actually load), and a server
    # that cannot be reached at all is dropped from BOTH the per-agent config and
    # its ADO Tool Bindings (below) — so agents are never told to use absent tools.
    # Always on (best-effort / fail-open); there is nothing to warm under
    # dry-run/simulate. Per-server verdicts are recorded in trace["mcpPrewarm"].
    unreachable_servers: set[str] = set()
    mcp_prewarm_trace: list[Mapping[str, Any]] | None = None
    if not getattr(args, "simulate", False) and not args.dry_run:
        to_warm = unique_servers(mcp_servers_by_key.values())
        if to_warm:
            verdicts = warm_and_probe(to_warm, log=lambda m: print(m, file=sys.stderr))
            unreachable_servers = unreachable(verdicts)
            mcp_prewarm_trace = []
            for name, res in sorted(verdicts.items(), key=lambda kv: kv[0]):
                entry: dict[str, Any] = {
                    "name": name,
                    "verdict": res.verdict,
                    "pruned": name in unreachable_servers,
                }
                if res.verdict != READY and res.stderr_tail:
                    entry["stderr"] = list(res.stderr_tail)  # diagnostic for a failed warm-up
                mcp_prewarm_trace.append(entry)
            if unreachable_servers:
                print(
                    f"[review] mcp-prewarm: dropping unreachable server(s) "
                    f"{sorted(unreachable_servers)} from prompts + config",
                    file=sys.stderr,
                )
                mcp_servers_by_key = {
                    key: prune_servers(cfg, unreachable_servers)
                    for key, cfg in mcp_servers_by_key.items()
                }
    # Per-agent ADO Tool Bindings block (Identity + Tool Bindings), rendered ONLY
    # for agents that opt into an ADO server via their `mcp:` graph binding AND only
    # when an ADO identity resolved. Non-ADO agents (and non-ADO repos) get nothing —
    # the usage prose degrades gracefully on the absent `## ADO Repository Identity`.
    ado_context_by_key = {}
    if identities:
        for key, servers in mcp_needs.items():
            ado_srv = [s for s in ado_servers(servers) if s not in unreachable_servers]
            if ado_srv:
                allowed_tools = custom_agents[key].tools or ()
                ado_context_by_key[key] = render_ado_context_for_servers(
                    identities, ado_srv, allowed_tools
                )
    print(
        f"[review] repo={repo.name} branch={target.branch} "
        f"agents={agent_count} context_chars={len(context)}",
        file=sys.stderr,
    )

    simulate = getattr(args, "simulate", False)
    if args.dry_run and not simulate:
        print(
            f"[review] dry-run: would review '{label}' with {agent_count} agents "
            f"-> artifacts under {base_dir}",
            file=sys.stderr,
        )
        print(f"DRY-RUN ok: {repo.name} ({label})")
        return 0
    if simulate:
        print(
            f"[review] SIMULATE: mock LLM (no tokens) — full scheduler + injection "
            f"+ dossier assembled; per-agent contexts dumped under {base_dir}",
            file=sys.stderr,
        )

    run_cwd = str(workspace.path) if workspace is not None else None
    git_context_record = _git_context(
        workspace,
        add_dirs=add_dirs,
        cwd=run_cwd,
        changed_files=changed_files,
    )
    try:
        result = run_review(
            label=label,
            config=configuration,
            backend_name=getattr(args, "_backend_name", "mock" if simulate else "copilot"),
            session_header=session_header,
            base_dir=base_dir,
            session_id=session_id,
            dump_prompts=args.dump_prompts or simulate,
            bundle_root=bundle_root(),
            repo_name=repo.name,
            source_branch=target.branch,
            ado_context_by_key=ado_context_by_key or None,
            mcp_servers_by_key=mcp_servers_by_key or None,
            mcp_prewarm=mcp_prewarm_trace,
            changed_files=changed_files,
            source_payloads=source_payloads,
            add_dirs=add_dirs,
            cwd=run_cwd,
            session_reuse=getattr(args, "session_reuse", True) and not simulate,
            max_attempts=review_cfg.max_attempts,
            concurrency=review_cfg.concurrency,
            subject=subject,
            diff_stat=_diff_stat(context, changed_files),
            provenance=_provenance(args, review_cfg.as_trace()),
            git_context=git_context_record,
            replay_context=replay_context.to_dict(),
        )
    except Exception as err:
        print(f"[review] pipeline failed: {err}", file=sys.stderr)
        return EXIT_ERROR

    result_callback = getattr(args, "_result_callback", None)
    if result_callback is not None:
        result_callback(result)

    if simulate:
        print(f"SIMULATE ok: {repo.name} ({label}) -> {result.persist.session_dir}")
        _print_session_links(result.persist)
        return 0

    if getattr(args, "_backend_name", "copilot") != "mock":
        _record_pr_adoption(result, target.pr, subject, configuration)

    # Minimal stdout: the verdict line (stdout reserved for the headline result).
    print(
        f"{result.verdict.verdict_icon} {result.verdict.verdict}  "
        f"session={result.persist.session_dir.name}"
    )
    _print_session_links(result.persist)

    if getattr(args, "publish", False):
        publish_code = _chained_publish(args, result.persist.session_dir)
        # Operational publish failure (no-creds → ABORTED, failed/ambiguous post →
        # ERROR) dominates the review verdict; a clean publish leaves the review's
        # verdict exit code intact.
        if publish_code in (EXIT_ERROR, EXIT_ABORTED):
            return publish_code

    return result.exit_code


def _record_pr_adoption(
    result,
    pr: PrReference | None,
    subject: Mapping[str, Any],
    configuration: Configuration,
) -> None:
    if not isinstance(pr, PrReference):
        return
    from roundtable.ado import build_review_record, record_review

    try:
        record = build_review_record(result, pr, subject, configuration)
        report = record_review(record, pr, result.persist.session_dir)
    except (OSError, RuntimeError, ValueError) as err:
        failure = f"{type(err).__name__}: {err}"
        print(
            f"[review] WARNING — adoption recording could not be prepared: {failure}",
            file=sys.stderr,
        )
        return
    if report.ok:
        print(
            f"[review] adoption: record=persisted label={report.label_action}",
            file=sys.stderr,
        )
        return
    print(
        "[review] WARNING — adoption recording failed; the review verdict is unchanged. "
        f'Retry with: roundtable adoption retry "{result.persist.session_dir}"',
        file=sys.stderr,
    )


def _link(path: Path) -> str:
    """A ``file://`` URI — clickable in modern terminals, pasteable everywhere else.

    Falls back to the plain path for a location that has no URI form (a UNC or
    relative path), so the line is always usable.
    """
    try:
        return path.resolve().as_uri()
    except ValueError:
        return str(path)


def write_report_html(session: Path, out: Path | None = None) -> Path:
    """Render a persisted session to a self-contained HTML report and return its path.

    The one renderer: the ``report`` subcommand and the end of every review both
    come through here, so a review's report is never a different artifact from the
    one ``roundtable report`` would produce.
    """
    from roundtable.persistence import build_graph_snapshot

    from .reporting import build_report

    target = out if out is not None else session / "report.html"
    target.write_text(build_report(session, graph_provider=build_graph_snapshot), encoding="utf-8")
    return target


def _print_session_links(persist) -> None:
    """Report where the run landed: artifacts dir, verdict, and the HTML report.

    The report is rendered here so it exists for every completed review without a
    second command. Rendering reads only persisted artifacts, so a failure costs the
    report and nothing else — it must never change the review's outcome.
    """
    print(f"[review] artifacts: {_link(persist.session_dir)}", file=sys.stderr)
    print(f"[review] verdict:   {_link(persist.report_path)}", file=sys.stderr)
    try:
        report = write_report_html(persist.session_dir)
    except Exception as err:
        print(f"[review] report.html not rendered: {err}", file=sys.stderr)
        return
    print(f"[review] report:    {_link(report)}", file=sys.stderr)


def _chained_publish(args: argparse.Namespace, session_dir: Path) -> int:
    """Publish the just-reviewed session in-line with ``review --publish``.

    Guards: only PR reviews can publish (``--pr`` required); ``--dry-run`` and
    ``--simulate`` reviews already returned before this point but are re-guarded
    defensively so a fabricated/absent session can never post. Delegates to the
    shared ``_run_publish_for_session`` helper (same gate + posting path as the
    standalone ``publish`` command), always targeting the reviewed PR.
    """
    if not getattr(args, "pr", None):
        print(
            "[review] --publish ignored: publishing requires a PR review (--pr); "
            "local-branch reviews have no PR to comment on.",
            file=sys.stderr,
        )
        return EXIT_CLEAN
    if getattr(args, "dry_run", False) or getattr(args, "simulate", False):
        print(
            "[review] --publish skipped: no live review ran (--dry-run/--simulate).",
            file=sys.stderr,
        )
        return EXIT_CLEAN

    print("[review] --publish: posting findings to the reviewed PR…", file=sys.stderr)
    options = PublishOptions(
        min_severity=args.publish_min_severity,
        dry_run=args.publish_dry_run,
        out_path=args.publish_out,
        pr_override=None,
    )
    return _run_publish_for_session(str(session_dir), options)


def _configured_sink(command: str) -> Publisher | None:
    """The configured output sink, or ``None`` (having said why) when there is none.

    ``sink: null`` declares that publishing is not part of this topology's contract —
    an optional step each config owns. Asking for it is a usage error against THAT
    config, not a crash.

    Once a sink exists, registers the config's bundle plugins: ``publish``/
    ``unpublish`` are their own CLI invocations, so nothing has imported the bundle
    yet, and the sink resolves that config's ``projector:`` by name from a registry
    those plugins populate. A config with no sink resolves nothing, so it imports
    nothing.
    """
    from roundtable.delivery import get_publisher
    from roundtable.graph import get_configuration, register_config_plugins

    configuration = get_configuration()
    publisher = get_publisher(
        getattr(configuration, "publisher", getattr(configuration, "sink", None))
    )
    if publisher is None:
        print(
            f"[{command}] this configuration declares no output sink (`sink: null`); "
            f"it does not publish its artifacts. Nothing to {command}.",
            file=sys.stderr,
        )
        return None
    register_config_plugins(get_configuration())
    return publisher


def _run_publish_for_session(
    session_dir: str,
    options: PublishOptions,
) -> int:
    """Publish one review session's findings via the configured sink; report outcome.

    Shared by the standalone ``publish`` command and the chained ``review
    --publish`` path. Dispatches the destination-specific pipeline to the sink the
    config's ``sink:`` key selects (``get_sink(config.sink).publish``). Returns the
    sink's exit code, or ``EXIT_BAD_ARGS`` when the config declares no sink.
    """
    delegated = _delegate_artifact_operation("publish", session_dir, options)
    if delegated is not None:
        return delegated
    try:
        _require_review_domain_values(get_configuration(), feature="roundtable publish")
    except ValueError as err:
        print(f"[publish] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    from roundtable.delivery import PublishRequest

    publisher = _configured_sink("publish")
    if publisher is None:
        return EXIT_BAD_ARGS
    return publisher.publish(PublishRequest(Path(session_dir), options=options)).exit_code


def _evaluation_repo_from_remote(remote_url: str) -> EvaluationRepository:
    parsed = parse_ado_remote_url(remote_url)
    if parsed is None:
        raise EvaluationError(
            f"evaluation PRs require an Azure DevOps git remote; could not parse {remote_url}"
        )
    canonical_url = ado_clone_url(
        PrReference(
            org=parsed.org,
            project=parsed.project,
            repo_name=parsed.repo_name,
            pr_id=0,
            host=parsed.host,
        )
    )
    return EvaluationRepository(
        org=parsed.org,
        project=parsed.project,
        name=parsed.repo_name,
        host=parsed.host,
        remote_url=canonical_url,
    )


def _pr_evaluation_revision(args: argparse.Namespace) -> EvaluationRevision:
    if args.base:
        raise EvaluationError("--base applies only to --from-commit")
    pr = parse_pr_reference(args.from_pr)
    metadata = fetch_pr_metadata(pr)
    source_sha = args.at_commit or metadata.source_commit_sha
    return EvaluationRevision(
        repository=_evaluation_repo_from_remote(ado_clone_url(pr)),
        source_sha=source_sha,
        base_sha=metadata.target_commit_sha,
        source_label=(
            f"PR {pr.pr_id} at {source_sha[:12]}" if args.at_commit else f"PR {pr.pr_id}"
        ),
        mode="pr-checkpoint" if args.at_commit else "pr-tip",
        requested_sha=args.at_commit,
        source_tip_sha=metadata.source_commit_sha,
        resolved_pr_id=pr.pr_id,
    )


def _evaluation_repository_input(repo_arg: str) -> tuple[EvaluationRepository, str | None]:
    repo_candidate = Path(repo_arg).expanduser()
    local_repo: str | None = None
    if repo_candidate.exists():
        repos = detect_repos(str(repo_candidate.resolve()))
        if not repos or not repos[0].remote_url:
            raise EvaluationError(f"could not resolve a git remote from {repo_candidate}")
        local_repo = repos[0].root_path
        remote_url = repos[0].remote_url
    else:
        remote_url = repo_arg
    return _evaluation_repo_from_remote(remote_url), local_repo


def _merge_evaluation_revision(
    args: argparse.Namespace,
    repository: EvaluationRepository,
) -> EvaluationRevision:
    if args.base or args.at_commit:
        raise EvaluationError("--from-merge-commit cannot be combined with --base or --at-commit")
    pr, metadata = fetch_pr_by_merge_commit(
        PrReference(
            org=repository.org,
            project=repository.project,
            repo_name=repository.name,
            pr_id=0,
            host=repository.host,
        ),
        args.from_merge_commit,
    )
    return EvaluationRevision(
        repository=repository,
        source_sha=metadata.source_commit_sha,
        base_sha=metadata.target_commit_sha,
        source_label=f"merge {args.from_merge_commit[:12]} (PR {pr.pr_id})",
        mode="merge-commit",
        requested_sha=args.from_merge_commit,
        source_tip_sha=metadata.source_commit_sha,
        resolved_pr_id=pr.pr_id,
        merge_commit_sha=args.from_merge_commit,
    )


def _explicit_evaluation_revision(
    args: argparse.Namespace,
    repository: EvaluationRepository,
) -> EvaluationRevision:
    if not args.base:
        raise EvaluationError("--base is required with --from-commit")
    if args.at_commit:
        raise EvaluationError("--at-commit applies only to --from-pr")
    return EvaluationRevision(
        repository=repository,
        source_sha=args.from_commit,
        base_sha=args.base,
        source_label=f"commit {args.from_commit[:12]}",
        mode="explicit-base",
        requested_sha=args.from_commit,
    )


def _resolve_evaluation_source(
    args: argparse.Namespace,
) -> tuple[EvaluationRevision, str | None]:
    if args.from_pr:
        return _pr_evaluation_revision(args), None
    if not args.repo:
        raise EvaluationError("--repo is required with commit inputs")
    repository, local_repo = _evaluation_repository_input(args.repo)
    revision = (
        _merge_evaluation_revision(args, repository)
        if args.from_merge_commit
        else _explicit_evaluation_revision(args, repository)
    )
    return revision, local_repo


def _evaluation_review_args(
    workspace_path: Path,
    base_sha: str,
    args: argparse.Namespace,
    callback: Any,
) -> argparse.Namespace:
    return argparse.Namespace(
        repo=str(workspace_path),
        base_branch=base_sha,
        pr=None,
        artifacts_dir=args.artifacts_dir,
        dry_run=False,
        backend=None,
        simulate=False,
        input_from=None,
        dump_prompts=args.dump_prompts,
        session_reuse=args.session_reuse,
        max_attempts=args.max_attempts,
        concurrency=args.concurrency,
        publish=False,
        publish_min_severity=None,
        publish_dry_run=False,
        publish_out=None,
        hint=None,
        hint_path=None,
        _runtime_capabilities=getattr(args, "_runtime_capabilities", None),
        _result_callback=callback,
    )


def _resolve_evaluation_revision(
    revision: EvaluationRevision,
    workspace_path: Path,
) -> EvaluationRevision:
    source_sha = resolve_commit(workspace_path, revision.source_sha)
    base_sha = resolve_commit(workspace_path, revision.base_sha)
    source_tip_sha = (
        resolve_commit(workspace_path, revision.source_tip_sha) if revision.source_tip_sha else None
    )
    resolved = EvaluationRevision(
        repository=revision.repository,
        source_sha=source_sha,
        base_sha=base_sha,
        source_label=revision.source_label,
        mode=revision.mode,
        requested_sha=revision.requested_sha,
        source_tip_sha=source_tip_sha,
        resolved_pr_id=revision.resolved_pr_id,
        merge_commit_sha=revision.merge_commit_sha,
    )
    if revision.mode.startswith("pr-") and source_tip_sha:
        validate_pr_checkpoint(
            workspace_path,
            checkpoint_sha=source_sha,
            source_tip_sha=source_tip_sha,
            target_sha=base_sha,
        )
    return resolved


def _run_evaluation_workspace(
    args: argparse.Namespace,
    revision: EvaluationRevision,
    workspace_path: Path,
):
    revision = _resolve_evaluation_revision(revision, workspace_path)
    captured: list[Any] = []
    review_args = _evaluation_review_args(
        workspace_path,
        revision.base_sha,
        args,
        captured.append,
    )

    def review(operation_revision: EvaluationRevision) -> EvaluationReview:
        review_args.base_branch = operation_revision.base_sha
        exit_code = _cmd_review(review_args)
        if not captured:
            raise EvaluationError(f"review did not produce a persisted session (exit {exit_code})")
        if exit_code not in (EXIT_CLEAN, EXIT_FINDINGS):
            raise EvaluationError(
                f"review failed with exit {exit_code}; no remote refs were pushed"
            )
        result = captured[0]
        return EvaluationReview(
            session_dir=result.persist.session_dir,
            verdict=result.verdict.verdict,
            exit_code=result.exit_code,
        )

    return run_evaluation(
        revision,
        name=args.name,
        review=review,
        push=lambda rev, source, target: push_exact_refs(
            rev,
            source,
            target,
            repo_path=workspace_path,
        ),
        create=create_draft_pull_request,
        publish=lambda session, pr_url: _run_publish_for_session(
            str(session),
            PublishOptions(pr_override=pr_url),
        ),
    )


def _resolve_evaluation_workspace(
    revision: EvaluationRevision,
    local_repo: str | None,
    temp_dir: str,
):
    return build_review_workspace(
        WorkspaceRequest(
            mode="pr",
            repo_path=local_repo,
            remote_url=revision.repository.remote_url,
            source_sha=revision.source_sha,
            base_sha=revision.base_sha or None,
        ),
        settings=get_settings().workspace,
        worktree_dir=Path(temp_dir) / "worktree",
        contextual_dirs=(str(Path.cwd()),),
    )


def _cmd_eval_pr(args: argparse.Namespace) -> int:
    """Review an exact historic diff before creating its evaluation-only draft PR."""
    try:
        revision, local_repo = _resolve_evaluation_source(args)
        capabilities = _resolve_runtime_preflight_or_report("eval-pr", get_configuration())
        if capabilities is None:
            return EXIT_ERROR
        args._runtime_capabilities = capabilities
        with tempfile.TemporaryDirectory(prefix="roundtable-eval-") as temp_dir:
            workspace = _resolve_evaluation_workspace(revision, local_repo, temp_dir)
            try:
                outcome = _run_evaluation_workspace(args, revision, workspace.path)
            finally:
                workspace.cleanup()
    except (EvaluationError, GitError, WorkspaceError, ValueError, OSError) as err:
        print(f"[eval-pr] {err}", file=sys.stderr)
        return EXIT_ERROR

    status = "complete" if outcome.complete else "partial"
    print(
        json.dumps(
            {
                "status": status,
                "pr": outcome.draft.url,
                "session": str(outcome.review.session_dir),
                "verdict": outcome.review.verdict,
                "mode": outcome.revision.mode,
                "sourceSha": outcome.revision.source_sha,
                "baseSha": outcome.revision.base_sha,
                "requestedCommit": outcome.revision.requested_sha,
                "resolvedPrId": outcome.revision.resolved_pr_id,
                "sourceRef": outcome.source_ref,
                "targetRef": outcome.target_ref,
                "publishExitCode": outcome.publish_exit_code,
            },
            indent=2,
        )
    )
    return EXIT_CLEAN if outcome.complete else outcome.publish_exit_code


def _cmd_eval_cleanup(args: argparse.Namespace) -> int:
    """Reclaim the scratch refs and draft PRs an evaluation left behind."""
    try:
        repository, _ = _evaluation_repository_input(args.repo)
        names = sweep_names(list_evaluation_refs(repository)) if args.all else (args.name,)
        outcome = run_teardown(
            teardown_targets(names),
            find_pull_requests=lambda ref: find_evaluation_pull_requests(repository, ref),
            abandon=lambda pr_id: abandon_pull_request(repository, pr_id),
            delete_ref=lambda ref: delete_evaluation_ref(repository, ref),
            dry_run=args.dry_run,
        )
    except (EvaluationError, GitError, ValueError, OSError) as err:
        print(f"[eval-cleanup] {err}", file=sys.stderr)
        return EXIT_ERROR

    print(
        json.dumps(
            {
                "status": "planned" if outcome.dry_run else "complete",
                "repository": repository.name,
                "evaluations": list(names),
                "abandonedPullRequests": list(outcome.abandoned_pr_ids),
                "deletedRefs": list(outcome.deleted_refs),
                "absentRefs": list(outcome.absent_refs),
            },
            indent=2,
        )
    )
    return EXIT_CLEAN


def _require_review_domain_values(configuration: Configuration, *, feature: str) -> None:
    if configuration.domain_values is None:
        raise ValueError(
            f"{feature} requires `domain_values` for severity and verdict; "
            f"configuration {configuration.name!r} declares none"
        )
    configuration.domain_values.require("severity", feature=feature)
    configuration.domain_values.require("verdict", feature=feature)


def _cmd_publish(args: argparse.Namespace) -> int:
    """Publish findings to an ADO PR — gated on count parity.

    Pipeline: extract publish plan (overlay × specialist
    index) → count-parity ABORT (on the full plan) → resolve the target PR from the
    persisted provenance (or ``--pr`` override, identity-checked) → resolve the
    reviewed PR iteration (best-effort) so inline threads auto-track forward →
    ``--min-severity`` view → render the unified comment per finding (inline where a
    changed-file anchor + iteration resolve, else a general thread with an
    enumerated terminal warning) → ``--dry-run`` writes ``threads.json`` or
    live-posts inline threads then the executive summary. Exits nonzero if any
    thread failed or was left ambiguous.
    """
    options = PublishOptions(
        min_severity=args.min_severity,
        dry_run=args.dry_run,
        out_path=args.out,
        pr_override=args.pr,
        allow_config_drift=args.allow_config_drift,
    )
    return _run_publish_for_session(args.session, options)


def _cmd_unpublish(args: argparse.Namespace) -> int:
    """Remove a session's published findings from its PR — the dual of publish.

    Builds the sink-specific options from the CLI flags and dispatches to the
    configured sink's ``unpublish`` (``get_sink(config.sink)``). No parity
    gate — a session must still be retractable. ``--dry-run``
    previews the matches without deleting. Exits nonzero if any delete failed.
    """
    options = UnpublishOptions(
        dry_run=args.dry_run,
        pr_override=args.pr,
        out_path=args.out,
    )
    return _run_unpublish_for_session(args.session, options)


def _cmd_run(args: argparse.Namespace) -> int:
    from roundtable.application import load_inputs, run_configuration

    try:
        inputs = load_inputs(args.input)
        completed = run_configuration(
            get_configuration(),
            inputs,
            backend=args.backend,
            output_dir=args.out,
            session_id=args.session_id,
            concurrency=args.concurrency or DEFAULT_CONCURRENCY,
            max_attempts=args.max_attempts or DEFAULT_MAX_ATTEMPTS,
            cwd=args.cwd,
        )
    except (OSError, RuntimeError, ValueError) as err:
        print(f"[run] {err}", file=sys.stderr)
        return EXIT_ERROR
    print(
        json.dumps(
            {
                "session": str(completed.persist.session_dir),
                "complete": completed.persist.exit_code == 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return completed.persist.exit_code


def _cmd_adoption_collect(args: argparse.Namespace) -> int:
    from roundtable.adoption import AdoptionCollector, collect_to

    if args.pr is not None and args.repository is None:
        print("[adoption collect] --pr requires --repository", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        collect_to(
            AdoptionCollector(args.org, args.project),
            args.out,
            repository=args.repository,
            pull_request_id=args.pr,
            since=args.since,
            until=args.until,
            quiet=args.quiet,
        )
    except KeyboardInterrupt:
        print("[adoption collect] interrupted", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, RuntimeError, ValueError) as err:
        print(f"[adoption collect] {err}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_CLEAN


def _cmd_adoption_query(args: argparse.Namespace) -> int:
    from roundtable.adoption import query_jsonl, render_table

    try:
        result = query_jsonl(args.input, args.metric, args.group_by)
    except (OSError, ValueError) as err:
        print(f"[adoption query] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    if args.format == "json":
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(render_table(result))
    return EXIT_CLEAN


def _cmd_adoption_report(args: argparse.Namespace) -> int:
    from roundtable.adoption import write_report

    try:
        output = write_report(args.input, args.out)
    except (OSError, ValueError) as err:
        print(f"[adoption report] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    print(output)
    if args.open:
        import webbrowser

        webbrowser.open(output.resolve().as_uri())
    return EXIT_CLEAN


def _cmd_adoption_retry(args: argparse.Namespace) -> int:
    from roundtable.ado import retry_record

    try:
        report = retry_record(args.session)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as err:
        print(f"[adoption retry] {err}", file=sys.stderr)
        return EXIT_ERROR
    print(
        json.dumps(
            {
                "status": "complete" if report.ok else "failed",
                "sessionId": report.record.session_id,
                "summaryStatus": report.summary.status if report.summary else "failed",
                "labelAction": report.label_action,
            },
            sort_keys=True,
        )
    )
    return EXIT_CLEAN if report.ok else EXIT_ERROR


def _run_unpublish_for_session(session_dir: str, options: UnpublishOptions) -> int:
    """Unpublish through the session's configuration-selected sink."""
    delegated = _delegate_artifact_operation("unpublish", session_dir, options)
    if delegated is not None:
        return delegated
    from roundtable.delivery import RetractRequest

    publisher = _configured_sink("unpublish")
    if publisher is None:
        return EXIT_BAD_ARGS
    return publisher.retract(RetractRequest(Path(session_dir), options=options)).exit_code


def _delegate_artifact_operation(
    operation: str, session_dir: str, options: PublishOptions | UnpublishOptions
) -> int | None:
    """Run under the session bundle, using a fresh process when the bundle differs."""
    from dataclasses import asdict

    from roundtable.bundle import config_root
    from roundtable.graph import (
        ConfigurationIdentityError,
        resolve_session_configuration,
    )

    try:
        identity = resolve_session_configuration(session_dir)
    except ConfigurationIdentityError as err:
        print(
            f"[{operation}] BLOCKED — cannot resolve session configuration: {err}", file=sys.stderr
        )
        return EXIT_BAD_ARGS
    if identity.root == config_root().resolve():
        from roundtable.graph import require_compatible_fingerprint

        if operation == "publish":
            try:
                drift = require_compatible_fingerprint(
                    identity, allow_drift=getattr(options, "allow_config_drift", False)
                )
            except ConfigurationIdentityError as err:
                print(f"[publish] BLOCKED — {err}", file=sys.stderr)
                return EXIT_BAD_ARGS
            if drift is not None:
                print(
                    "[publish] WARNING — configuration fingerprint drift was explicitly "
                    f"allowed (recorded={drift[0]}, current={drift[1]}).",
                    file=sys.stderr,
                )
        else:
            drift = require_compatible_fingerprint(identity, allow_drift=True)
            if drift is not None:
                print(
                    "[unpublish] WARNING — configuration fingerprint drift detected "
                    f"(recorded={drift[0]}, current={drift[1]}); continuing so the "
                    "session remains retractable.",
                    file=sys.stderr,
                )
        return None

    env = os.environ.copy()
    env[ENV_VAR] = str(identity.root)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "roundtable.artifact_worker",
            operation,
            session_dir,
            json.dumps(asdict(options)),
        ],
        env=env,
        check=False,
    )
    return completed.returncode


def _cmd_view(args: argparse.Namespace) -> int:
    """Inspect a persisted review session (read-only; no LLM calls).

    Prints a concise summary of ``trace.json`` (verdict, counts).
    """
    from roundtable.settings import resolve_artifact_path

    try:
        session = resolve_artifact_path(args.session)
    except ValueError as err:
        print(f"[view] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    trace_path = session / "trace.json"
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"[view] could not read {trace_path}: {err}", file=sys.stderr)
        return EXIT_BAD_ARGS

    sid = trace.get("sessionId", session.name)
    verdict = trace.get("verdict", "?")
    icon = trace.get("verdictIcon", "")
    counts = trace.get("counts") or {}
    print(f"session: {sid}")
    print(f"verdict: {verdict} {icon}".rstrip())
    print(f"agents: {trace.get('agentCount', '?')} | counts: {counts if counts else '(none)'}")

    return EXIT_CLEAN


def _print_tool_surface(surface: dict) -> bool:
    """Print each agent's grant beside its usage; return whether a grant was violated."""
    violated = False
    for key in sorted(surface):
        s = surface[key]
        if not s.used and not s.grant_known:
            continue
        used = ", ".join(f"{t}×{c}" for t, c in sorted(s.used.items(), key=lambda kv: -kv[1]))
        granted = ", ".join(sorted(s.granted)) if s.grant_known else "(not recorded)"
        print(f"{key}:")
        print(f"  granted: {granted}")
        print(f"  used:    {used or '(none)'}")
        if s.granted_unused:
            print(f"  unused:  {', '.join(s.granted_unused)}")
        if s.used_outside_grant:
            violated = True
            print(f"  OUTSIDE GRANT: {', '.join(s.used_outside_grant)}")
    return violated


def _cmd_tools(args: argparse.Namespace) -> int:
    """Show a session's per-agent tool surface, optionally diffed against another.

    Read-only and offline. Without ``--against`` this prints the graph's grant
    beside the tools the run actually reached, and exits non-zero if an agent
    called a tool its grant does not cover. With it, the exit code reports
    whether any agent's tool composition moved between the two runs — the signal
    that an SDK-side tool-surface change produces and a graph diff cannot.
    """
    from roundtable.reporting import diff_tool_surface, read_tool_surface

    if args.refresh_inventory:
        return _refresh_tool_inventory()
    if not args.session:
        print("[tools] a session path is required", file=sys.stderr)
        return EXIT_BAD_ARGS

    try:
        after = read_tool_surface(args.session)
        before = read_tool_surface(args.against) if args.against else None
    except (FileNotFoundError, OSError, ValueError) as err:
        print(f"[tools] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS

    if before is None:
        return EXIT_ERROR if _print_tool_surface(after) else EXIT_CLEAN

    shifted = [d for d in diff_tool_surface(before, after) if d.shifted]
    if not shifted:
        print("[tools] no tool-composition shift between the two runs")
        return EXIT_CLEAN
    for d in shifted:
        moves = []
        if d.added:
            moves.append(f"+{', +'.join(d.added)}")
        if d.removed:
            moves.append(f"-{', -'.join(d.removed)}")
        print(f"{d.key}: {' '.join(moves)} ({d.calls_before} → {d.calls_after} calls)")
    return EXIT_ERROR


def _refresh_tool_inventory() -> int:
    """Maintainer-only refresh of the packaged static tool baseline."""
    from datetime import date

    from roundtable.capabilities import (
        resolve_runtime_capabilities,
        write_inventory,
    )

    print("[tools] maintainer refresh: probing runtime capabilities...")
    try:
        capabilities = resolve_runtime_capabilities(force_tool_probe=True)
    except Exception as err:
        print(f"[tools] probe failed: {err}", file=sys.stderr)
        return EXIT_ERROR
    path = write_inventory(
        sorted(capabilities.builtin_tool_ids),
        sorted(capabilities.always_on_tool_ids),
        capabilities.cli_version,
        date.today().isoformat(),
    )
    print(
        f"[tools] wrote packaged baseline {path}: "
        f"{len(capabilities.builtin_tool_ids)} built-in, "
        f"{len(capabilities.always_on_tool_ids)} always-on"
    )
    return EXIT_CLEAN


def _cmd_report(args: argparse.Namespace) -> int:
    """Render a persisted session into a self-contained interactive HTML report.

    Read-only and offline (no LLM calls). Visualizes run metadata, the agent
    execution DAG, and each agent's system/context/response. Opens the result in
    a browser unless ``--no-open`` is given.
    """
    import webbrowser

    from roundtable.settings import resolve_artifact_path

    try:
        session = resolve_artifact_path(args.session)
    except ValueError as err:
        print(f"[report] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        out = write_report_html(session, Path(args.output) if args.output else None)
    except FileNotFoundError as err:
        print(f"[report] {err}", file=sys.stderr)
        return EXIT_BAD_ARGS
    except OSError as err:
        print(f"[report] could not write the report: {err}", file=sys.stderr)
        return EXIT_ERROR

    print(f"report: {_link(out)}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return EXIT_CLEAN


def _cmd_index(args: argparse.Namespace) -> int:
    """Rebuild a repo folder's derived ``index.json`` from its session traces.

    A repair/inspection path: reviews rebuild the index automatically, but this
    regenerates it (e.g. after a manual session move/delete) by scanning every
    ``<repo_dir>/*/trace.json``.
    """
    repo_dir = Path(args.repo_dir).expanduser()
    if not repo_dir.is_dir():
        print(f"[index] not a directory: {repo_dir}", file=sys.stderr)
        return EXIT_BAD_ARGS
    index_path = write_repo_index(repo_dir)
    trace = json.loads(index_path.read_text(encoding="utf-8"))
    print(f"[index] wrote {index_path} ({len(trace.get('reviews', []))} session(s))")
    return EXIT_CLEAN


def _cmd_prune(args: argparse.Namespace) -> int:
    """Delete session directories older than ``--older-than`` days, then reindex.

    A session id is ``session_<UTCstamp>_<slug>``; the leading stamp is parsed to
    decide age, so no per-session metadata read is needed. Each repo folder whose
    sessions changed has its ``index.json`` rebuilt afterwards.
    """
    import shutil
    from datetime import UTC, datetime, timedelta

    repo_dir = Path(args.repo_dir).expanduser()
    if not repo_dir.is_dir():
        print(f"[prune] not a directory: {repo_dir}", file=sys.stderr)
        return EXIT_BAD_ARGS
    cutoff = datetime.now(UTC) - timedelta(days=args.older_than)
    removed = 0
    for child in sorted(repo_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not child.name.startswith("session_"):
            continue
        stamp = child.name.split("_")[1] if len(child.name.split("_")) > 1 else ""
        try:
            when = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        if when < cutoff:
            if args.dry_run:
                print(f"[prune] would remove {child}")
            else:
                shutil.rmtree(child, ignore_errors=True)
                print(f"[prune] removed {child}")
            removed += 1
    if removed and not args.dry_run:
        write_repo_index(repo_dir)
    print(f"[prune] {'would remove' if args.dry_run else 'removed'} {removed} session(s)")
    return EXIT_CLEAN


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question on the terminal; default No. Non-interactive ⇒ No."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


_NO_UPDATE_CHECK_ENV = "ROUNDTABLE_NO_UPDATE_CHECK"


def _check_for_update(updater, source: str) -> int:
    """Report whether a newer version is published, without installing anything.

    Exit ``EXIT_CLEAN`` when up to date / not applicable / the check could not run
    (fail-soft — a network or auth hiccup must not fail a script), and
    ``EXIT_ERROR`` when a strictly newer version exists so CI/prompts can detect
    "behind". Honours ``ROUNDTABLE_NO_UPDATE_CHECK`` as an opt-out.
    """
    if with_legacy_fallback(os.environ).get(_NO_UPDATE_CHECK_ENV):
        print(f"update: version check disabled ({_NO_UPDATE_CHECK_ENV} is set)")
        return EXIT_CLEAN
    if updater.current_install_is_editable():
        print(f"update: editable/source install ({__version__}) — version check n/a")
        return EXIT_CLEAN
    try:
        available = updater.list_remote_versions(source)
    except updater.UpdateError as err:
        print(f"update: could not check for updates: {err}", file=sys.stderr)
        return EXIT_CLEAN
    newer = updater.is_outdated(__version__, available)
    if newer is None:
        print(f"update: up to date ({__version__} is the latest at {source})")
        return EXIT_CLEAN
    print(
        f"update: a newer version is available: {newer} (you have {__version__}).\n"
        "        Upgrade with:  roundtable update"
    )
    return EXIT_ERROR


def _cmd_update(args: argparse.Namespace) -> int:
    """Update the installed roundtable to the latest (or a specific) feed version.

    Reinstalls via ``pip install --upgrade --index-url <feed> roundtable==<X.Y.Z>``.
    ``--list`` shows published versions; ``--dry-run`` prints the exact pip command
    without running it. Refuses on an editable/source install (use git there).
    """
    from . import updater

    settings = get_settings()
    if args.source is not None:
        try:
            source = validate_update_source(args.source, source_name="--source")
        except ConfigError as err:
            print(f"update: {err}", file=sys.stderr)
            return EXIT_BAD_ARGS
        source_layer = "flag:--source"
    else:
        source, source_layer = (
            settings.update.source,
            settings.sources.get("update.source", "default"),
        )
    if not source:
        print(
            "update: no source is configured. Pass --source, set "
            "ROUNDTABLE_UPDATE_SOURCE, configure update.source in roundtable.yaml, "
            "or bootstrap ADO updates with --update-source.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    print(f"update: source = {source}  [{source_layer}]", file=sys.stderr)

    if args.check:
        return _check_for_update(updater, source)

    if args.list:
        try:
            available = updater.list_remote_versions(source)
        except updater.UpdateError as err:
            print(f"update: {err}", file=sys.stderr)
            return EXIT_ERROR
        versions = updater.sort_versions_desc(available)
        if not versions:
            print(f"update: no versions published to the feed yet ({source})")
            return EXIT_CLEAN
        print(f"Available versions at {source}:")
        for version in versions:
            print(f"  {version}")
        return EXIT_CLEAN

    if updater.current_install_is_editable():
        print(
            "update: this is an editable/source install — it *is* your working tree, so "
            "there is nothing to update.\n"
            "        Use git to switch versions:  git fetch && git checkout <branch-or-ref>",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    try:
        available = updater.list_remote_versions(source)
        plan = updater.resolve_plan(args.version, source, available)
    except updater.UpdateError as err:
        print(f"update: {err}", file=sys.stderr)
        return EXIT_BAD_ARGS

    label = f"{plan.version} (latest)" if plan.is_latest else plan.version
    print(f"update: {__version__} -> {label}  (from {source})")

    if args.dry_run:
        print("update: dry run — would run:\n  " + " ".join(plan.command))
        return EXIT_CLEAN

    if not args.yes and not _confirm(f"Reinstall {updater.PACKAGE_NAME} at {plan.version}?"):
        print("update: cancelled.")
        return EXIT_ABORTED

    pid = updater.launcher_pid()
    if pid is not None:
        # Windows console-script launcher run: pip can't replace the live
        # roundtable.exe while this process holds it. Hand off to a detached
        # helper that waits for the launcher to exit, then runs pip. Success here
        # means *launched*, not *installed* — the status file carries pip's result.
        try:
            handoff = updater.start_detached_update(plan, pid)
        except OSError as err:
            print(
                f"update: could not launch the background updater: {err}\n"
                "        Run this manually instead:\n  " + " ".join(plan.command),
                file=sys.stderr,
            )
            return EXIT_ERROR
        print(
            f"update: installing {plan.version} in the background "
            "(this launcher must exit first to release its .exe).\n"
            f"        log:    {handoff.log_path}\n"
            f"        status: {handoff.status_path}\n"
            "        Re-run `roundtable --version` in a few seconds to confirm."
        )
        return EXIT_CLEAN

    code = updater.run_pip(plan.command)
    if code != 0:
        print(f"update: pip exited {code}", file=sys.stderr)
        return EXIT_ERROR
    print(f"update: installed {plan.version}. It takes effect on the next `roundtable` run.")
    return EXIT_CLEAN


def _review_skill_path(args: argparse.Namespace) -> Path:
    return Path(args.destination) if args.destination else review_skill_destination()


def _print_review_skill_status(result: Mapping[str, str]) -> None:
    print(json.dumps(dict(result), sort_keys=True))


def _cmd_install_review(args: argparse.Namespace) -> int:
    try:
        result = install_review_skill(_review_skill_path(args), force=args.force)
    except (OSError, ValueError) as err:
        print(f"roundtable install-review: {err}", file=sys.stderr)
        return EXIT_ERROR
    _print_review_skill_status(result)
    return EXIT_CLEAN


def _cmd_status_review(args: argparse.Namespace) -> int:
    _print_review_skill_status(review_skill_status(_review_skill_path(args)))
    return EXIT_CLEAN


def _cmd_uninstall_review(args: argparse.Namespace) -> int:
    try:
        result = uninstall_review_skill(_review_skill_path(args), force=args.force)
    except (OSError, ValueError) as err:
        print(f"roundtable uninstall-review: {err}", file=sys.stderr)
        return EXIT_ERROR
    _print_review_skill_status(result)
    return EXIT_CLEAN


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Validate the agent graph (agent_graph.yaml) against the prompt bundle.

    Runs the consolidated static check (graph integrity + prompt presence +
    declared model coverage + schema↔gate coherence), the import boundaries and
    the rebrand guard. Normal mode resolves authenticated model and built-in tool
    availability. The hidden static mode is reserved for deterministic CI.
    """
    settings = get_settings()
    if settings.source_path is not None:
        print(f"doctor: config loaded from {settings.source_path}")
    else:
        print("doctor: no roundtable.yaml found — env vars + built-in defaults only")
    print(render_params(settings_params(settings), title="doctor: resolved settings"))
    runtime_capabilities = None
    if not getattr(args, "static", False):
        runtime_capabilities = _resolve_runtime_capabilities_or_report("doctor")
        if runtime_capabilities is None:
            return EXIT_ERROR
    report = validate_agents(
        bundle_root(),
        runtime_capabilities=runtime_capabilities,
    )
    boundary_violations = check_context_import_boundary()
    boundary_violations += check_persistence_import_boundary()
    boundary_violations += check_validation_import_boundary()
    boundary_violations += check_rebrand_guard()
    if not report.ok or boundary_violations:
        if not report.ok:
            print(report.failure_text, file=sys.stderr)
        if boundary_violations:
            print("doctor: import-boundary violations:", file=sys.stderr)
            for v in boundary_violations:
                print(f"  - {v}", file=sys.stderr)
        return EXIT_ERROR
    for w in report.warnings:
        print(f"doctor: warning: {w}", file=sys.stderr)
    n = len(graph_custom_agents(bundle_root()))
    print(
        f"doctor: agent graph OK — {len(_graph_keys())} entries, {n} agents route a declared model."
    )
    return EXIT_CLEAN


def _graph_keys() -> list[str]:
    from roundtable.graph import get_configuration

    return [e.key for e in get_configuration().entries]


def _positive_int(text: str) -> int:
    """argparse type for a flag that must be a whole integer >= 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    # The severity floor and its default both belong to THIS configuration. The
    # engine supplies no semantic fallback for labels or scale positions.
    configuration = get_configuration()
    severity_values = (
        configuration.domain_values.get("severity")
        if configuration.domain_values is not None
        else None
    )
    severities = list(severity_values or ())
    default_min_severity = configuration.publishing.default_min_severity
    default_min_severity_help = default_min_severity or "all severities"
    # The resolved env>file>default values are shown in --help only; the flags
    # themselves default to None so an explicit flag is detectable, and the full
    # flag>env>file>default precedence is resolved once in config.effective at run
    # time (not split between argparse defaults and call-site fallbacks).
    default_max_attempts = get_settings().max_attempts or DEFAULT_MAX_ATTEMPTS
    default_concurrency = get_settings().concurrency or DEFAULT_CONCURRENCY
    p = argparse.ArgumentParser(
        prog="roundtable",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show the installed roundtable version and exit",
    )
    sub = p.add_subparsers(dest="command", required=True)

    generic_run = sub.add_parser(
        "run",
        help="Run a configuration with caller-supplied source inputs",
    )
    generic_run.add_argument(
        "--input",
        action="append",
        required=True,
        metavar="KEY=PATH",
        help="source-node key and UTF-8 payload path; repeat for each source",
    )
    generic_run.add_argument("--backend", default="copilot", help="registered backend name")
    generic_run.add_argument("--out", required=True, help="artifact directory")
    generic_run.add_argument("--session-id", default=None, help="optional artifact session id")
    generic_run.add_argument("--cwd", default=None, help="backend working directory")
    generic_run.add_argument("--concurrency", type=_positive_int, default=None)
    generic_run.add_argument("--max-attempts", type=_positive_int, default=None)
    generic_run.set_defaults(func=_cmd_run)

    ip = sub.add_parser(
        "inputs", help="Preview the diff and context a review would use (no AI, no changes)"
    )
    ip.add_argument("repo", help="path to the local repo checkout to review")
    ip.add_argument(
        "--base-branch",
        default=None,
        help=f"base branch/ref to diff against (default: {UNSET_FALLBACK['base_branch']})",
    )
    ip.add_argument(
        "--pr",
        default=None,
        help="ADO PR URL or numeric id (PR mode; auth via your 'az login', or a "
        "PAT in ROUNDTABLE_ADO_PAT/AZURE_DEVOPS_PAT)",
    )
    ip.set_defaults(func=_cmd_inputs)

    rv = sub.add_parser(
        "review",
        help="Review a local branch or a pull request and save the verdict + report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "modes:\n"
            "  local      review [repo]             review the working tree (repo: current dir)\n"
            "  PR by URL  review --pr <url>         find or clone the repo, then review the PR\n"
            "  PR by id   review <repo> --pr <id>   review the PR from an existing local clone\n"
        ),
    )
    rv.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="local repo to review (default: current directory; omit for a --pr URL)",
    )
    rv.add_argument(
        "--base-branch",
        default=None,
        help=f"base branch/ref to diff against (default: {UNSET_FALLBACK['base_branch']})",
    )
    rv.add_argument(
        "--pr",
        default=None,
        help="review a PR instead of the working tree. A full ADO URL finds/clones "
        "the repo automatically; a numeric id needs the matching local repo. Auth: "
        "'az login' or a PAT in ROUNDTABLE_ADO_PAT/AZURE_DEVOPS_PAT",
    )
    rv.add_argument(
        "--artifacts-dir",
        default=None,
        help="base dir for session artifacts (default: ~/roundtable/artifacts)",
    )
    rv.add_argument(
        "--dry-run",
        action="store_true",
        help="set everything up (build the diff, install agents) but skip the "
        "AI review — a fast way to check the command works",
    )
    backend_group = rv.add_mutually_exclusive_group()
    backend_group.add_argument(
        "--backend",
        default=None,
        metavar="REGISTERED_NAME",
        help="run with a registered backend (default: copilot)",
    )
    backend_group.add_argument(
        "--simulate",
        action="store_true",
        help="run the whole pipeline with a fake AI that returns canned answers "
        "(no tokens, no cost). Produces a complete session you can inspect "
        "with `view`. Great for trying it out or debugging.",
    )
    rv.add_argument(
        "--input-from",
        default=None,
        metavar="SESSION_DIR",
        help="replay a complete prior session from its recorded commit and workspace "
        "context; cannot be combined with repo, --pr, or --base-branch",
    )
    rv.add_argument(
        "--dump-prompts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="save each agent's exact prompt and reply under <session>/agents/ "
        "(for debugging what the agents saw). On by default; --no-dump-prompts to skip",
    )
    rv.add_argument(
        "--session-reuse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="after a schema-backed submission rejection, resume the same Copilot "
        "session with feedback only (the diff stays live in-session). On by default; "
        "--no-session-reuse starts a fresh session for each validation attempt.",
    )
    rv.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=None,
        help="maximum attempts for a schema-backed agent to make an accepted output "
        "submission. Schema-less agents return their first successful response. Also settable via "
        "roundtable.yaml (max_attempts) / ROUNDTABLE_MAX_ATTEMPTS. "
        f"Default: {default_max_attempts}",
    )
    rv.add_argument(
        "--concurrency",
        type=_positive_int,
        default=None,
        help="how many agents run at once (executor thread-pool width). Agents wait on "
        "a subprocess, so this is an I/O-parallelism knob, not a CPU one. Also settable "
        "via roundtable.yaml (concurrency) / ROUNDTABLE_CONCURRENCY. "
        f"Default: {default_concurrency}",
    )
    rv.add_argument(
        "--publish",
        action="store_true",
        help="after a successful PR review, immediately post the findings as "
        "Azure DevOps PR comment threads (equivalent to running `publish` on the "
        "resulting session). Requires --pr; ignored for local-branch, --dry-run, "
        "and --simulate reviews.",
    )
    rv.add_argument(
        "--publish-min-severity",
        default=default_min_severity,
        choices=severities or None,
        help="with --publish: only post comment threads for findings at or above "
        f"this severity (default: {default_min_severity_help}). Same semantics as "
        "`publish --min-severity` — gates every per-finding thread (inline and general), "
        "not just inline anchors.",
    )
    rv.add_argument(
        "--publish-dry-run",
        action="store_true",
        help="with --publish: render the threads to threads.json instead of posting "
        "(the review still runs live).",
    )
    rv.add_argument(
        "--publish-out",
        default=None,
        help="with --publish --publish-dry-run: path for the threads.json "
        "(default: <session>/threads.json).",
    )
    rv.add_argument(
        "--hint",
        default=None,
        metavar="TEXT",
        help="author-supplied context/rationale injected into every agent's "
        "## Author Context (e.g. 'the module-level logger is reset in finally — "
        "intentional'). Weighed as context, never overrides analysis. Capped; "
        "long text is truncated.",
    )
    rv.add_argument(
        "--hint-path",
        default=None,
        metavar="PATH",
        help="path to a file OR directory the author flags as relevant context. "
        "Rendered as a pointer (agents read it on demand) rather than inlined, so it "
        "does not bloat every agent's context. The hint is copied into a private "
        "per-session dir that agents may read — no other file on your machine is "
        "exposed. A directory is copied whole (VCS metadata and symlinks skipped) up "
        "to a size cap. Warns if the path is missing.",
    )
    rv.set_defaults(func=_cmd_review)

    ep = sub.add_parser(
        "eval-pr",
        help="Review an exact historic diff, then publish it through a draft PR",
        description=(
            "Review an exact cumulative PR or commit snapshot before any remote mutation. "
            "After the review completes, push exact-SHA scratch refs, create an "
            "evaluation-only draft PR, and publish the saved findings. Source and "
            "generated PR descriptions are not exposed to reviewers."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "remote side effects:\n"
            "  - pushes two refs under refs/heads/roundtable/eval/<name>/\n"
            "  - creates an always-draft PR with no auto-complete or work-item changes\n"
            "  - posts the saved findings using the bundle's default severity threshold\n"
        ),
    )
    evaluation_source = ep.add_mutually_exclusive_group(required=True)
    evaluation_source.add_argument(
        "--from-pr",
        metavar="ADO_PR_URL",
        help="full Azure DevOps PR URL; optionally stop at --at-commit",
    )
    evaluation_source.add_argument(
        "--from-merge-commit",
        metavar="SHA",
        help="Azure DevOps merge commit used to recover the completed PR source/base pair",
    )
    evaluation_source.add_argument(
        "--from-commit",
        metavar="SHA",
        help="cumulative source snapshot; requires an explicit --base",
    )
    ep.add_argument(
        "--repo",
        help="with commit inputs: local repo path or Azure DevOps clone URL",
    )
    ep.add_argument(
        "--at-commit",
        metavar="SHA",
        help="with --from-pr: review the PR cumulatively only up to this source-history commit",
    )
    ep.add_argument(
        "--base",
        metavar="SHA_OR_REF",
        help="with --from-commit: explicit cumulative diff base",
    )
    ep.add_argument(
        "--name",
        required=True,
        help="unique name used under refs/heads/roundtable/eval/",
    )
    ep.add_argument(
        "--artifacts-dir",
        default=None,
        help="base dir for session artifacts (default: ~/roundtable/artifacts)",
    )
    ep.add_argument(
        "--dump-prompts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="save each agent's exact prompt and reply (default: enabled)",
    )
    ep.add_argument(
        "--session-reuse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="reuse Copilot sessions for structured-output retries (default: enabled)",
    )
    ep.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=None,
        help=f"maximum structured-output attempts (default: {default_max_attempts})",
    )
    ep.add_argument(
        "--concurrency",
        type=_positive_int,
        default=None,
        help=f"agent concurrency (default: {default_concurrency})",
    )
    ep.set_defaults(func=_cmd_eval_pr)

    ec = sub.add_parser(
        "eval-cleanup",
        help="Reclaim the scratch refs and draft PRs an evaluation left behind",
        description=(
            "Abandon the evaluation draft PRs for one or all evaluations in a repository, "
            "then delete the scratch refs that backed them. Abandoning is not deleting: an "
            "abandoned PR keeps its published threads and resolves its commits, so "
            "reclaiming the branch namespace costs none of the evaluation's evidence."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "remote side effects:\n"
            "  - abandons (never deletes) evaluation draft PRs\n"
            "  - deletes refs under refs/heads/roundtable/eval/ and nothing else\n"
            "  - use --dry-run first; --all sweeps every evaluation in the repository\n"
        ),
    )
    ec.add_argument(
        "--repo",
        required=True,
        help="local repo path or Azure DevOps clone URL",
    )
    cleanup_scope = ec.add_mutually_exclusive_group(required=True)
    cleanup_scope.add_argument(
        "--name",
        help="the evaluation name used under refs/heads/roundtable/eval/",
    )
    cleanup_scope.add_argument(
        "--all",
        action="store_true",
        help="reclaim every evaluation in the repository",
    )
    ec.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be abandoned and deleted without changing anything",
    )
    ec.set_defaults(func=_cmd_eval_cleanup)

    pb = sub.add_parser(
        "publish",
        help="Post a reviewed session's findings as Azure DevOps PR comment "
        "threads (only if the session passed its integrity checks)",
    )
    pb.add_argument("session", help="path to a review session dir (the one containing trace.json)")
    pb.add_argument(
        "--pr",
        default=None,
        help="target PR (id or full ADO URL) — overrides the session's persisted PR; "
        "must be the same repository that was reviewed",
    )
    pb.add_argument(
        "--min-severity",
        default=default_min_severity,
        choices=severities or None,
        help="only post comment threads for findings at or above this severity "
        f"(default: {default_min_severity_help}); this gates every per-finding thread "
        "(inline and general), not just inline anchors. Lower-severity findings still "
        "appear in the executive-summary thread. View only — parity always runs on the "
        "full plan.",
    )
    pb.add_argument(
        "--dry-run",
        action="store_true",
        help="render the threads and write them to threads.json instead of posting",
    )
    pb.add_argument(
        "--out",
        default=None,
        help="path for the --dry-run threads.json (default: <session>/threads.json)",
    )
    pb.add_argument(
        "--allow-config-drift",
        action="store_true",
        help="publish with a changed resolved configuration bundle after auditing "
        "the recorded/current fingerprint warning",
    )
    pb.set_defaults(func=_cmd_publish)

    up = sub.add_parser(
        "unpublish",
        help="Remove a session's published findings from its Azure DevOps PR "
        "(deletes only Roundtable's own watermarked comments)",
    )
    up.add_argument("session", help="path to a review session dir (the one containing trace.json)")
    up.add_argument(
        "--pr",
        default=None,
        help="target PR (id or full ADO URL) — overrides the session's persisted PR; "
        "must be the same repository that was reviewed",
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="list the comments that would be deleted to unpublish.json instead of deleting",
    )
    up.add_argument(
        "--out",
        default=None,
        help="path for the --dry-run unpublish.json (default: <session>/unpublish.json)",
    )
    up.set_defaults(func=_cmd_unpublish)

    adoption = sub.add_parser(
        "adoption",
        help="Collect, query, or retry Roundtable PR adoption records",
    )
    adoption_commands = adoption.add_subparsers(dest="adoption_command", required=True)
    collect = adoption_commands.add_parser(
        "collect",
        help="Collect versioned review records from one Azure DevOps project",
    )
    collect.add_argument("--org", required=True, help="Azure DevOps organization")
    collect.add_argument("--project", required=True, help="Azure DevOps project")
    collect.add_argument("--repository", default=None, help="limit collection to one repository")
    collect.add_argument(
        "--pr", type=int, default=None, help="collect one PR; requires --repository"
    )
    collect.add_argument("--since", default=None, help="include records at or after this ISO date")
    collect.add_argument("--until", default=None, help="include records at or before this ISO date")
    collect.add_argument("--out", default=None, help="write JSONL to this path (default: stdout)")
    collect.add_argument("--quiet", action="store_true", help="suppress per-page progress")
    collect.set_defaults(func=_cmd_adoption_collect)

    query = adoption_commands.add_parser(
        "query",
        help="Aggregate a locally collected adoption JSONL file",
    )
    query.add_argument("--input", required=True, help="collected JSONL file")
    query.add_argument("--metric", required=True, choices=("prs", "reviews", "findings"))
    query.add_argument(
        "--group-by",
        required=True,
        choices=(
            "project",
            "repository",
            "configurationName",
            "installationSource",
            "toolVersion",
            "verdict",
            "severity",
            "category",
            "agent",
        ),
    )
    query.add_argument("--format", choices=("table", "json"), default="table")
    query.set_defaults(func=_cmd_adoption_query)

    report = adoption_commands.add_parser(
        "report",
        help="Render collected adoption JSONL as a self-contained HTML dashboard",
    )
    report.add_argument("--input", required=True, help="collected JSONL file")
    report.add_argument("--out", required=True, help="output HTML file")
    report.add_argument("--open", action="store_true", help="open the report after writing it")
    report.set_defaults(func=_cmd_adoption_report)

    retry = adoption_commands.add_parser(
        "retry",
        help="Retry a failed review-record summary and label write",
    )
    retry.add_argument("session", help="path to the review session directory")
    retry.set_defaults(func=_cmd_adoption_retry)

    vw = sub.add_parser(
        "view", help="Show a saved review's verdict and findings (read-only, offline)"
    )
    vw.add_argument(
        "session",
        help="absolute path or artifacts-root-relative review session containing trace.json",
    )
    vw.set_defaults(func=_cmd_view)

    tl = sub.add_parser(
        "tools",
        help="Show a saved review's per-agent tool surface, or diff it against another run",
    )
    tl.add_argument(
        "session", nargs="?", help="path to a review session dir (the one containing trace.json)"
    )
    tl.add_argument(
        "--against",
        default=None,
        metavar="SESSION",
        help="an earlier session to compare against; exits non-zero on a composition shift",
    )
    tl.add_argument(
        "--refresh-inventory",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    tl.set_defaults(func=_cmd_tools)

    rp = sub.add_parser(
        "report",
        help="Render a saved review into an interactive offline HTML report (read-only)",
        description=(
            "Render a saved review into an interactive offline HTML report (read-only). "
            "Reports and session artifacts can include local paths, repository or PR URLs, "
            "branches, commit identifiers, prompts, and full agent responses. Inspect and "
            "redact them before sharing."
        ),
    )
    rp.add_argument(
        "session",
        help="absolute path or artifacts-root-relative review session containing trace.json",
    )
    rp.add_argument(
        "-o", "--output", default=None, help="output HTML path (default: <session>/report.html)"
    )
    rp.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="open the report in a browser when done (default: yes)",
    )
    rp.set_defaults(func=_cmd_report)

    ix = sub.add_parser(
        "index",
        help="Rebuild a repo folder's derived index.json from its session traces (offline)",
    )
    ix.add_argument(
        "repo_dir",
        help="path to a per-repo artifacts folder (e.g. ~/roundtable/artifacts/<repo-slug>)",
    )
    ix.set_defaults(func=_cmd_index)

    pr = sub.add_parser(
        "prune",
        help="Delete session dirs older than N days in a repo folder, then reindex (offline)",
    )
    pr.add_argument("repo_dir", help="path to a per-repo artifacts folder")
    pr.add_argument(
        "--older-than",
        type=int,
        required=True,
        metavar="DAYS",
        help="remove sessions whose UTC timestamp is older than this many days",
    )
    pr.add_argument(
        "--dry-run", action="store_true", help="list what would be removed without deleting"
    )
    pr.set_defaults(func=_cmd_prune)

    up = sub.add_parser(
        "update",
        help="Update this installed roundtable from a configured PyPI simple index",
    )
    up.add_argument(
        "version",
        nargs="?",
        default="latest",
        help="version to install: 'latest' (default) or an explicit version like 1.0.0",
    )
    up.add_argument(
        "--source",
        default=None,
        help="override the feed simple-index URL; default from env/workspace/user settings",
    )
    up.add_argument(
        "--list", action="store_true", help="list the versions available at the source and exit"
    )
    up.add_argument(
        "--check",
        action="store_true",
        help="check whether a newer version is published (exit non-zero if behind); installs nothing",
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact pip command that would run, without executing it",
    )
    up.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation prompt before reinstalling"
    )
    up.set_defaults(func=_cmd_update)

    skill = sub.add_parser(
        "skill",
        help="Manage the optional user-scoped Roundtable review skill",
    )
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    for command, help_text, handler in (
        (
            "install-review",
            "Install the packaged Roundtable review skill for explicit user requests",
            _cmd_install_review,
        ),
        (
            "status-review",
            "Report whether the installed Roundtable review skill is absent, current, stale, or diverged",
            _cmd_status_review,
        ),
        (
            "uninstall-review",
            "Remove the installed Roundtable review skill",
            _cmd_uninstall_review,
        ),
    ):
        skill_parser = skill_commands.add_parser(command, help=help_text)
        skill_parser.add_argument(
            "--destination",
            default=None,
            help="override the skill directory (default: ~/.copilot/skills/roundtable-review)",
        )
        if command != "status-review":
            skill_parser.add_argument(
                "--force",
                action="store_true",
                help="overwrite or remove a locally modified installation",
            )
        skill_parser.set_defaults(func=handler)

    dc = sub.add_parser(
        "doctor",
        help="Validate setup and authenticated runtime capabilities before a review",
    )
    dc.add_argument(
        "--static",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    dc.set_defaults(func=_cmd_doctor)

    # One declaration, applied to every subcommand — no subcommand can drift, and a
    # new one cannot forget it. main() has already bound the root from raw argv
    # (build_parser itself reads the bundle, above), so parsing it here documents and
    # validates the flag rather than acting on it.
    for subparser in sub.choices.values():
        subparser.add_argument(
            CONFIG_FLAG,
            default=None,
            metavar="NAME|PATH",
            help="configuration bundle to use — a shipped name "
            f"({', '.join(shipped_bundles())}) or a directory containing "
            f"agent_graph.yaml (default: ${ENV_VAR} if set, else buddies)",
        )
    return p


CONFIG_FLAG = "--config"


def _bind_config_root(argv: list[str] | None) -> None:
    """Resolve ``--config`` from raw argv, before anything reads a bundle.

    argparse cannot own this: :func:`build_parser` reads the bundle itself (the
    severity floor's choices), so the root must be settled first. The flag is still
    declared on every subcommand, so ``--help`` lists it and a typo is rejected the
    usual way.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    value: str | None = None
    for i, token in enumerate(tokens):
        if token == CONFIG_FLAG:
            value = tokens[i + 1] if i + 1 < len(tokens) else None
            break
        if token.startswith(f"{CONFIG_FLAG}="):
            value = token.split("=", 1)[1]
            break
    else:
        return
    if not value:
        raise ConfigRootError(f"{CONFIG_FLAG} needs a bundle name or path")
    set_config_root(resolve_bundle(value))


def main(argv: list[str] | None = None) -> int:
    # stdout is reserved for structured output; force UTF-8 so diff/JSON content
    # is byte-faithful regardless of the host console code page (Windows cp1252
    # would otherwise raise UnicodeEncodeError on non-latin1 diff bytes).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")
    migrate_legacy_home_dir()  # rebrand-compat: one-time ~/InspectorX-py -> ~/roundtable
    try:
        _bind_config_root(argv)
        parser = build_parser()
    except (ConfigError, ConfigRootError) as err:
        print(f"roundtable: {err}", file=sys.stderr)
        return EXIT_ERROR
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
