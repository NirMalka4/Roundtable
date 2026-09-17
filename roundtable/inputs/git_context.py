"""git_context: the unified diff gatherer.
Single code path for local worktrees, clone worktrees, and live checkouts.

Produces the oracle-compatible git-context string (header + changed files +
unified diff), per-file git history, the rename-aware changed-file list, and
:class:`ContextMetadata` for reproducibility (``snapshot_sha`` / ``base_branch`` /
``base_sha``).

FIDELITY NOTES:
  * Diff uses ``-M`` (rename detection) and the **three-dot** range
    ``<target_ref>...HEAD`` — the same command the recorded oracle sessions used.
  * The header string format is stable so the (already-built) context
    builder's ``Base:`` / ``-- Diff --`` parsing keeps working.
  * ``base_sha`` is the **merge-base** of ``target_ref`` and HEAD — the commit the
    three-dot diff evaluates against. This is the SHA that publish replays against
    in the *same local worktree* (reduction #5). ``None`` on merge-base failure;
    publish then fails loud rather than silently file-scoping comments.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from roundtable.runtime import APP_NAME

from ._gitexec import GitError, run_git

MAX_HISTORY_FILES = 20

Mode = Literal["local-worktree", "clone-worktree", "live-checkout"]

# Fallback when a caller omits ``mode``. The workspace resolver reports the
# authoritative mode for every ``--pr`` / URL review; the only mode-less caller is
# an in-place local review, which is a live checkout of the working tree's HEAD.
_DEFAULT_MODE: Mode = "live-checkout"


# ── Dataclasses ──────────────────────────────────
@dataclass
class ReviewContextOptions:
    worktree_path: str
    repo_name: str
    target_branch: str
    source_branch: str
    # Supplied by the ReviewWorkspace resolver: the exact reviewed revision and the
    # authoritative workspace mode. When ``source_sha`` is None the gatherer falls
    # back to ``HEAD`` (a live local checkout); when ``mode`` is None it infers from
    # the path (legacy callers). Both keep existing behaviour byte-identical.
    source_sha: str | None = None
    mode: Mode | None = None


@dataclass
class ContextMetadata:
    snapshot_sha: str
    base_branch: str
    source_branch: str
    diff_command: str
    snapshot_timestamp: str
    worktree_path: str
    changed_file_count: int
    diff_line_count: int
    mode: Mode
    base_sha: str | None


@dataclass
class ReviewContextResult:
    git_context: str
    git_history: str
    changed_files: list[str]
    metadata: ContextMetadata


# ── Ref resolution ──────────────────────────────────────────────────────────
def resolve_target_ref(cwd: str, branch: str) -> str:
    """Resolve a branch name to a ref that exists in ``cwd``'s object store.

    Probes ``origin/{b}`` (local clone), ``{b}`` (bare cache), and the explicit
    ref forms, in order of likelihood. Raises ``ValueError`` if none resolve.
    """
    clean_branch = re.sub(r"^refs/heads/", "", branch)
    clean_branch = re.sub(r"^origin/", "", clean_branch)

    candidates = [
        f"origin/{clean_branch}",
        clean_branch,
        f"refs/heads/{clean_branch}",
        f"refs/remotes/origin/{clean_branch}",
    ]

    for candidate in candidates:
        try:
            run_git(["rev-parse", "--verify", "--quiet", candidate], cwd, timeout=5.0)
            return candidate
        except GitError:
            continue
        except Exception:
            continue

    raise ValueError(
        f"Target ref '{clean_branch}' not found. Tried: {', '.join(candidates)}. "
        "Ensure the target branch is fetched."
    )


def resolve_default_base_branch(
    cwd: str,
    candidate_names: Sequence[str] = ("main", "master", "develop", "trunk"),
) -> str | None:
    """Detect a repository's default base branch (the single detection SoT).

    The diff base otherwise defaults to the literal ``'main'``, which silently
    degrades to an empty review on repos whose mainline is ``master``/``develop``
    (see :func:`resolve_target_ref`, which raises when ``main`` does not resolve).

    Resolution order:
      1. The remote's advertised default branch via
         ``git symbolic-ref --quiet --short refs/remotes/origin/HEAD`` —
         authoritative for cloned repos regardless of the branch name.
      2. Probe common default names as a remote-tracking (``origin/<name>``) or
         local (``<name>``) ref, so repos without an ``origin/HEAD`` symref — or
         without an ``origin`` remote at all — still resolve.

    Returns a *bare* branch NAME (no ``origin/`` prefix) suitable for
    :func:`resolve_target_ref`, or ``None`` when nothing matches.
    """
    # 1. Remote's advertised default branch (origin/HEAD symref).
    try:
        out = run_git(
            ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd,
            timeout=5.0,
        ).stdout.strip()
        name = re.sub(r"^origin/", "", out)
        if name:
            return name
    except Exception:
        pass  # origin/HEAD not set — fall through to probing common names

    # 2. Probe common names as remote-tracking or local refs.
    for name in candidate_names:
        for ref in (f"origin/{name}", name):
            try:
                run_git(["rev-parse", "--verify", "--quiet", ref], cwd, timeout=5.0)
                return name
            except Exception:
                continue
    return None


def determine_base_branch(cwd: str) -> str:
    """Back-compat adapter over :func:`resolve_default_base_branch` (the single
    base-branch detection SoT). Preserves the legacy contract: always returns a
    usable, ``origin/``-prefixed ref, falling back to ``origin/main`` when nothing
    is detected (e.g. a non-git directory).
    """
    detected = resolve_default_base_branch(cwd)
    return f"origin/{detected}" if detected else "origin/main"


# ── Changed-file extraction (rename-aware) ──────────────────────────────────
_DIFF_GIT_RE = re.compile(r"^diff --git a/.+? b/(.+)")


def extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff (new-path side for renames)."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.split("\n"):
        m = _DIFF_GIT_RE.match(line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            files.append(m.group(1))
    return files


# ── Helpers ─────────────────────────────────────────────────────────────────
def _resolve_sha(cwd: str, ref: str) -> str:
    """Resolve ``ref`` (a SHA or ``HEAD``) to a full commit SHA in ``cwd``."""
    return run_git(["rev-parse", f"{ref}^{{commit}}"], cwd, timeout=5.0).stdout.strip()


def _review_ref(opts: ReviewContextOptions) -> str:
    """The right-hand side of the three-dot diff: the reviewed revision.

    The workspace-provided ``source_sha`` when present (reproducible, unambiguous),
    else ``HEAD`` (a live local checkout reviews its committed tip).
    """
    return opts.source_sha or "HEAD"


def _effective_mode(opts: ReviewContextOptions) -> Mode:
    """Authoritative workspace mode when supplied, else the live-checkout default."""
    return opts.mode or _DEFAULT_MODE


_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def _compute_merge_base_sha(cwd: str, target_ref: str, snapshot_sha: str) -> str | None:
    """Merge-base of ``target_ref`` and ``snapshot_sha``; ``None`` on any failure."""
    try:
        out = run_git(["merge-base", target_ref, snapshot_sha], cwd, timeout=5.0).stdout.strip()
        sha = out.split("\n")[0].strip() if out else ""
        return sha if sha and _SHA_RE.match(sha) else None
    except Exception as err:
        print(
            f"[{APP_NAME}][gitcontext] merge-base({target_ref}, {snapshot_sha[:8]}) "
            f"failed in {cwd}: {err}",
            file=__import__("sys").stderr,
        )
        return None


def _capture_file_history(cwd: str, repo_name: str, files: list[str]) -> str:
    if not files:
        return ""
    parts: list[str] = [f"\n=== Repository: {repo_name} ==="]
    for file in files:
        try:
            out = run_git(["log", "--oneline", "-10", "--", file], cwd, timeout=15.0).stdout.strip()
            if out:
                parts.append(f"\n--- {file} ---\n{out}")
        except Exception:
            continue
    return "\n".join(parts) if len(parts) > 1 else ""


def _now_iso() -> str:
    # ISO-8601 UTC with millisecond precision and a 'Z' suffix.
    now = datetime.datetime.now(datetime.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _empty_result(
    opts: ReviewContextOptions,
    target_ref: str,
    snapshot_sha: str,
    diff_command: str,
    base_sha: str | None,
) -> ReviewContextResult:
    return ReviewContextResult(
        git_context="No changes detected.",
        git_history="",
        changed_files=[],
        metadata=ContextMetadata(
            snapshot_sha=snapshot_sha,
            base_branch=target_ref,
            source_branch=opts.source_branch,
            diff_command=diff_command,
            snapshot_timestamp=_now_iso(),
            worktree_path=opts.worktree_path,
            changed_file_count=0,
            diff_line_count=0,
            mode=_effective_mode(opts),
            base_sha=base_sha,
        ),
    )


# ── Main gatherer ───────────────────────────────────────────────────────────
def gather_review_context(opts: ReviewContextOptions) -> ReviewContextResult:
    """Gather review context from any worktree. Single code path for all modes.

    Mirrors ``gatherReviewContext``: on any error it degrades gracefully, returning
    the error text as ``git_context`` so downstream agents can still run.
    """
    try:
        return _gather_inner(opts)
    except Exception as err:
        fallback = (
            f"=== Repository: {opts.repo_name} | Base: {opts.target_branch} | "
            f"HEAD: {opts.source_branch} ===\nFailed to capture git context: {err}"
        )
        review_ref = _review_ref(opts)
        return ReviewContextResult(
            git_context=fallback,
            git_history="",
            changed_files=[],
            metadata=ContextMetadata(
                snapshot_sha=opts.source_sha or "unknown",
                base_branch=opts.target_branch,
                source_branch=opts.source_branch,
                diff_command=f"git diff -M {opts.target_branch}...{review_ref}",
                snapshot_timestamp=_now_iso(),
                worktree_path=opts.worktree_path,
                changed_file_count=0,
                diff_line_count=0,
                mode=_effective_mode(opts),
                base_sha=None,
            ),
        )


def _gather_inner(opts: ReviewContextOptions) -> ReviewContextResult:
    cwd = opts.worktree_path

    # Step 1: resolve target ref.
    target_ref = resolve_target_ref(cwd, opts.target_branch)

    # Step 2: snapshot SHA — the reviewed revision (workspace source_sha, else HEAD).
    review_ref = _review_ref(opts)
    snapshot_sha = _resolve_sha(cwd, review_ref)

    # Step 2b: merge-base SHA (anchors publish-time diff).
    base_sha = _compute_merge_base_sha(cwd, target_ref, snapshot_sha)

    # Step 3: diff command (rename detection, three-dot range anchored to the
    # reviewed revision — git computes merge-base(target, review) internally).
    diff_range = f"{target_ref}...{review_ref}"
    diff_command = f"git diff -M {diff_range}"

    name_only = run_git(["diff", "-M", "--name-only", diff_range], cwd, timeout=30.0).stdout
    changed_files = [ln for ln in name_only.strip().split("\n") if ln]

    if not changed_files:
        return _empty_result(opts, target_ref, snapshot_sha, diff_command, base_sha)

    raw_diff = run_git(["diff", "-M", diff_range], cwd, timeout=30.0).stdout

    # Step 4: format (oracle-compatible header).
    header = (
        f"=== Repository: {opts.repo_name} | Base: {target_ref} | HEAD: {opts.source_branch} ==="
    )
    git_context = (
        f"{header}\n-- Changed Files --\n{chr(10).join(changed_files)}\n\n"
        f"-- Diff --\n{raw_diff.strip()}"
    )

    # Step 5: per-file history (cap at MAX_HISTORY_FILES).
    git_history = _capture_file_history(cwd, opts.repo_name, changed_files[:MAX_HISTORY_FILES])

    # Step 6: metadata.
    metadata = ContextMetadata(
        snapshot_sha=snapshot_sha,
        base_branch=target_ref,
        source_branch=opts.source_branch,
        diff_command=diff_command,
        snapshot_timestamp=_now_iso(),
        worktree_path=cwd,
        changed_file_count=len(changed_files),
        diff_line_count=len(raw_diff.split("\n")),
        mode=_effective_mode(opts),
        base_sha=base_sha,
    )

    return ReviewContextResult(
        git_context=git_context,
        git_history=git_history,
        changed_files=changed_files,
        metadata=metadata,
    )
