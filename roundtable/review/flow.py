"""review.flow: end-to-end review transaction (graph → verdict → persist).

Ties graph execution to the verdict and persistence layers in a single,
injectable entry point:

    run_graph(source_payloads, run_fn)  →  compute_verdict(results)  →  persist_session()
                                                            →  process exit code

The LLM seam (``run_fn``) and the artifact base dir are injectable so the whole
flow is unit-testable with a fake runtime and a ``tmp_path`` — no live model and
no writes under the user's home. The CLI ``review`` subcommand wires the real
copilot subprocess + ``~/roundtable/artifacts``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roundtable.backend import (
    Backend,
    BackendOptions,
    BackendProvider,
    KeywordBackend,
    backend_context,
    format_ai_credits,
    merge_usage,
)
from roundtable.decision import (
    UNKNOWN,
    VerdictResult,
    extract_session_id,
    get_verdict_icon,
    render_verdict_md,
    verdict_to_exit_code,
)
from roundtable.delivery import get_report
from roundtable.engine import Engine, RunOptions, RunResult, _runnable_graph_entries, get_executor
from roundtable.extraction import parse_domain_result
from roundtable.graph import Configuration, get_configuration
from roundtable.persistence import PersistResult, persist_session
from roundtable.records import write_repo_index
from roundtable.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_ATTEMPTS,
    default_artifacts_root,
)

from .agent_inputs import ReviewAgentInputBuilder
from .trace_overlay import build_overlay

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def make_session_id(label: str, *, now: datetime | None = None) -> str:
    """``session_<UTCstamp>_<slug>`` — stable, filesystem-safe session id."""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
    slug = _SLUG_RE.sub("-", label).strip("-").lower() or "review"
    return f"session_{ts}_{slug}"


def repo_slug(name: str) -> str:
    """Filesystem-safe, readable per-repo folder name derived from ``repo.name``."""
    slug = _SLUG_RE.sub("-", name).strip("-").lower()
    return slug or "repo"


def _unknown_verdict(reason: str, session_dir_path: str) -> VerdictResult:
    """A config-agnostic UNKNOWN result naming why no domain result was readable."""
    return VerdictResult(
        verdict=UNKNOWN,
        verdict_icon=get_verdict_icon(UNKNOWN),
        verdict_overridden=False,
        reason=reason,
        session_id=extract_session_id(session_dir_path),
    )


def _read_domain_result(
    session_results: Mapping[str, Any],
    session_dir_path: str,
    config: Configuration,
) -> tuple[VerdictResult, dict[str, int] | None]:
    """Read the run's domain result (verdict + counts) from the sole runnable sink.

    The verdict / finding-count transform is a graph node (the terminal ``kind: code``
    sink), so the result is read **topologically** — the unique out-degree-0 node of
    the runnable graph, guaranteed by ``doctor``'s single-sink layer — with no
    hardcoded node name.

    A missing or malformed payload degrades to UNKNOWN naming the reason, rather than
    recomputing. Recomputing here would mean reading a terminal agent's shape from
    shared orchestration, which can only ever match ONE config: the config it was
    written for gets a silent rescue and every other gets a silent wrong answer. The
    sink node is the single producer of this contract; if it did not produce one,
    that is the honest result.
    """
    sink_keys = get_executor(config.executor).sinks(_runnable_graph_entries(config))
    sink = session_results.get(sink_keys[0]) if len(sink_keys) == 1 else None
    if sink is not None and getattr(sink, "valid", False) and getattr(sink, "response", ""):
        try:
            return parse_domain_result(sink.response, session_dir_path=session_dir_path)
        except (ValueError, KeyError) as err:
            return _unknown_verdict(f"Sink payload malformed: {err}", session_dir_path), None
    reason = (
        "No runnable sink node produced a domain result"
        if sink is None
        else "Sink node produced no valid response"
    )
    return _unknown_verdict(reason, session_dir_path), None


@dataclass
class ReviewResult:
    """Everything a caller needs: the verdict, the artifacts, the exit code."""

    verdict: VerdictResult
    persist: PersistResult
    scheduler: RunResult
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return self.persist.exit_code


def run_review(
    *,
    label: str,
    session_header: str = "",
    config: Configuration | None = None,
    backend_name: str | None = None,
    backend: Backend | None = None,
    run_fn: Any | None = None,
    base_dir: Path | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    add_dirs: list[str] | None = None,
    session_id: str | None = None,
    dump_prompts: bool = False,
    bundle_root: Path | None = None,
    repo_name: str | None = None,
    source_branch: str | None = None,
    ado_context_by_key: Mapping[str, str] | None = None,
    mcp_servers_by_key: Mapping[str, dict[str, dict] | None] | None = None,
    mcp_prewarm: list[Mapping[str, Any]] | None = None,
    changed_files: list[str] | None = None,
    source_payloads: Mapping[str, str] | None = None,
    session_reuse: bool = True,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    subject: Mapping[str, Any] | None = None,
    diff_stat: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    git_context: Mapping[str, Any] | None = None,
    replay_context: Mapping[str, Any] | None = None,
    cwd: str | None = None,
) -> ReviewResult:
    """Run the full review and persist its artifacts.

    Returns a :class:`ReviewResult` whose ``exit_code`` follows the canonical
    verdict→exit mapping (APPROVE=0, else 1; pipeline exceptions surface to the
    CLI which maps them to ERROR=2).

    ``session_header`` is the per-session constant (``## Change Under Review``)
    prepended verbatim to every agent context.

    ``repo_name``/``source_branch`` populate the ``verdict.md`` metadata header;
    when absent they render as ``Unknown``.

    ``dump_prompts`` (with ``bundle_root``) writes the opt-in per-agent T0 prompt
    dump (``<session>/agents/<key>/{system,context,response}.md`` + manifest) after
    persistence. Pure debug output — never affects the verdict or exit code.

    ``changed_files`` is the diff's changed-file list, available to configured
    output-validation gates on schema-backed agents. Omitted/empty ⇒ changed-file
    grounding is skipped.

    ``session_reuse`` (default ``True``) enables in-session retries after a
    schema-backed submission rejection. ``--no-session-reuse`` starts a fresh session
    for each validation attempt.

    ``max_attempts`` (default ``DEFAULT_MAX_ATTEMPTS``) bounds schema-backed submission
    attempts. Schema-less agents stop after their first successful backend response.
    The CLI resolves it via ``--max-attempts`` / ``ROUNDTABLE_MAX_ATTEMPTS`` /
    ``roundtable.yaml``.

    ``concurrency`` (default ``DEFAULT_CONCURRENCY``) is the executor's thread-pool
    width — how many agents run at once. The CLI resolves it via ``--concurrency`` /
    ``ROUNDTABLE_CONCURRENCY`` / ``roundtable.yaml``.

    ``subject`` (when given) records *what was reviewed* (repo / branch / PR / source
    SHA) into ``trace.json`` and triggers the per-repo derived index rebuild. The CLI
    threads it; a subject-less call (unit test) keeps the flat, index-free layout.
    ``diff_stat`` (change-set fingerprint) and ``provenance`` (reviewer version /
    agent-graph fingerprint / CLI invocation) are additive trace.json blocks the CLI
    threads for reproducibility; omitted ⇒ absent.

    ``cwd`` (when given) is the review workspace path, threaded down as the working
    directory of every agent subprocess so their shell ``git``/relative-path
    operations resolve against the reviewed tree. Omitted ⇒ the subprocess inherits
    Roundtable's own cwd (``--add-dir`` still grants read access either way).
    """
    sid = session_id or make_session_id(label)
    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    base = base_dir or default_artifacts_root()

    cfg = config or get_configuration()
    if backend_name is not None and (backend is not None or run_fn is not None):
        raise ValueError("backend_name cannot be combined with backend or run_fn")
    selected_backend = backend or KeywordBackend(run_fn or _missing_backend)

    def supplied_backend(_options: BackendOptions):
        return backend_context(selected_backend)

    provider: str | BackendProvider = backend_name or supplied_backend
    scheduler = Engine(
        provider,
        options=RunOptions(
            concurrency=concurrency,
            add_dirs=tuple(add_dirs or ()),
            session_reuse=session_reuse,
            max_attempts=max_attempts,
            cwd=cwd,
            changed_files=tuple(changed_files or ()),
            mcp_servers_by_key=mcp_servers_by_key,
            agent_input_builder=ReviewAgentInputBuilder(
                configuration=cfg,
                session_header=session_header,
                context_by_key=ado_context_by_key or {},
                changed_files=tuple(changed_files or ()),
                workspace=cwd,
            ),
        ),
    ).run(cfg, source_payloads or {})

    session_usage = merge_usage(
        o.usage for o in scheduler.results.values() if getattr(o, "usage", None) is not None
    )

    # The output CONTRACT is the graph's terminal ``Verdict`` sink node (kind: code /
    # build_verdict): the verdict + finding counts are computed *during* the run and
    # emitted as the domain-result payload on the sole runnable sink. Read it
    # topologically — no hardcoded node name — re-hydrating the VerdictResult +
    # counts. An absent/malformed payload degrades to UNKNOWN naming the reason; it
    # is never recomputed here, because recomputing means reading one config's agent
    # shape from shared orchestration.
    verdict, counts = _read_domain_result(scheduler.results, sid, cfg)

    # Render the human verdict.md through the config's own report renderer — the
    # only code that knows what THIS graph's Judge emits. Independent of the sink:
    # every run writes a report, publishing is optional. A config with no `report:`
    # (or a renderer that declines) falls back to the slim verdict stub.
    # Best-effort by contract: a render failure must never change a verdict.
    verdict_md: str | None = None
    integrity_warning: str | None = None
    try:
        reporter = get_report(cfg.report)
        if reporter is not None:
            rendered = reporter.render(
                verdict,
                counts,
                scheduler.results,
                repo_name=repo_name,
                source_branch=source_branch,
                session_id=sid,
            )
            if rendered is not None:
                verdict_md = rendered.markdown
                integrity_warning = rendered.integrity_warning
    except Exception as err:
        print(f"[review] verdict.md report render failed: {err}", file=sys.stderr)
    if integrity_warning is not None:
        print(f"[review] WARNING count-parity: {integrity_warning}", file=sys.stderr)

    # Assemble the review-domain overlay (verdict surface + finding counts +
    # subject / diff / provenance) merged into the neutral trace.json, and the human
    # report (the config's own renderer when it produced one, else the slim verdict
    # stub). The persistence core stays domain-agnostic; the review shell owns these.
    overlay = build_overlay(
        verdict,
        counts=counts,
        subject=subject,
        diff_stat=diff_stat,
        provenance=provenance,
    )
    report_md = verdict_md if verdict_md is not None else render_verdict_md(verdict, counts)

    usage_log = (
        f"[review] usage output_tokens={session_usage.output_tokens} rounds={session_usage.rounds}"
    )
    if session_usage.billing.is_complete:
        assert session_usage.billing.total_nano_aiu is not None
        usage_log += f" ai_credits={format_ai_credits(session_usage.billing.total_nano_aiu)}"

    persist = persist_session(
        base,
        session_id=sid,
        agent_outcomes=scheduler.results,
        overlay=overlay,
        started_at=started_at,
        report_md=report_md,
        report_filename="verdict.md",
        exit_code=verdict_to_exit_code(verdict.verdict),
        outcome_label=verdict.verdict,
        mcp_prewarm=mcp_prewarm,
        git_context=git_context,
        configuration=cfg,
        source_payloads=source_payloads,
        replay_context=replay_context,
        log_lines=[
            f"[review] session={sid}",
            f"[review] agents={len(scheduler.results)} order={'>'.join(scheduler.execution_order)}",
            f"[review] verdict={verdict.verdict} overridden={verdict.verdict_overridden}",
            *([f"[review] count-parity WARNING: {integrity_warning}"] if integrity_warning else []),
            usage_log,
        ],
    )

    # Rebuild the enclosing repo's derived index so the per-repo session list stays
    # current. Only when a subject was recorded (the CLI review path): a subject-less
    # run (unit test) keeps the flat, index-free layout. Best-effort — an index
    # rebuild failure never fails the review.
    if subject is not None:
        try:
            write_repo_index(base)
        except OSError as err:
            print(f"[review] index rebuild skipped: {err}", file=sys.stderr)

    if dump_prompts:
        _dump_prompts(persist.session_dir, scheduler.results, bundle_root, cfg)

    return ReviewResult(
        verdict=verdict,
        persist=persist,
        scheduler=scheduler,
        counts=counts or {},
    )


def _missing_backend(**_kwargs: Any):
    raise RuntimeError("no backend supplied")


def _dump_prompts(
    session_dir,
    agent_outcomes,
    bundle_root,
    config: Configuration,
) -> None:
    """Best-effort per-agent prompt dump (T0 parity). Never fails a review."""
    try:
        from roundtable.persistence import dump_session_prompts
        from roundtable.runtime import system_prompts_by_key

        root = bundle_root or config.root / "prompts" / "Reviewer"
        system_prompts = system_prompts_by_key(root, config.entries)
        dump_session_prompts(
            session_dir,
            agent_outcomes=agent_outcomes,
            system_prompts=system_prompts,
        )
    except Exception as err:
        import sys

        print(f"[review] prompt dump failed: {err}", file=sys.stderr)
