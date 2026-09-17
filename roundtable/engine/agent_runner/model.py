"""Result data model for per-agent backend execution and output submission.

``AttemptDetail`` is the per-iteration observability record; ``AgentRunOutcome`` is
the aggregate the run loop returns and the rest of the pipeline consumes. Both are
plain dataclasses with no behaviour, kept separate from the loop so downstream
modules (``dag_scheduler``, ``scheduler_common``, ``context.*``, ``persistence.trace``)
depend only on the shapes, not the retry machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from roundtable.backend import EMPTY_USAGE, AgentUsage, ExecutionPolicy


@dataclass
class AttemptDetail:
    """Per-attempt execution and submission observability record.

    One entry per backend turn. ``errors`` and ``warnings`` are
    the *sole* structured diagnostics for the attempt: the single carrier for
    everything that went wrong on it. Consumers derive any summary they need on
    read — the first failing gate is ``errors[0]["gate"]`` and the reject reason is
    the joined ``errors`` messages.

    Each entry is a ``{...}`` mapping of one of two shapes:

    * **Gate diagnostic** — a retention-safe projection of a rejected schema-backed
      submission. Exact candidate values remain only in live model feedback.
    * **Runtime reason** — ``{kind, message, snippet?}`` where ``kind`` is the
      specific non-gate failure cause, such as ``api_error`` — the same
      value as this attempt's ``outcome``. It carries no gate; the ``kind`` marker
      makes it unambiguously distinct from a gate ``message``.

    Warnings are captured even on a passing attempt (they are advisory, non-blocking).

    ``retry_feedback`` is the artifact-safe projection of the incremental corrective
    steering prepended to this attempt's prompt. Schema submission values are removed
    from this projection; the model still receives the exact correction in process.
    It is empty on attempt 1.
    """

    attempt: int
    model: str
    """The model this attempt *asked* for (the graph's ``model:``). Compare with
    ``observed_model`` — the runtime substitutes rather than refuses, so the two
    agreeing is a fact worth recording, not an assumption."""
    outcome: (
        str  # submission_valid | submission_rejected | submission_missing | raw_output | api_error
    )
    observed_model: str = ""
    """The model the runtime actually billed, from ``assistant.usage.model``.
    Comma-joined on the (unexpected) occasion a single attempt spans several."""
    retry_feedback: str = ""
    tools_invoked: list[str] = field(default_factory=list)
    """Tool names invoked during *this* attempt's subprocess, in request order
    (may repeat). Empty when the attempt requested no tools. Names record which
    tool ran and — by the ``<server>-`` prefix — whether it was builtin or MCP.
    Name-only projection persisted to ``trace.json``."""
    tool_calls: list[dict] = field(default_factory=list)
    """Redacted ``{name, args?, ok?, toolError?}`` calls made during this attempt.

    Structured failure provenance remains separate from attempt validation/runtime
    errors unless the tool failure itself terminates the attempt.
    """
    wall_clock_ms: float = 0.0
    tool_calls_truncated: bool = False
    tool_calls_omitted_count: int = 0
    events: list[dict] = field(default_factory=list)
    events_truncated: bool = False
    events_omitted_count: int = 0
    """Wall-clock duration of this attempt's ``copilot`` subprocess in
    milliseconds (``CopilotResult.wall_clock_s`` × 1000). Includes tool/MCP time
    the API-duration usage field excludes."""
    usage: AgentUsage = EMPTY_USAGE
    timeout_phase: str | None = None
    """Timeout location reported by the backend, when this attempt timed out."""
    timeout_snapshot: list[dict] = field(default_factory=list)
    """Safe active tool/background facts captured before timeout cleanup."""
    execution_policy: ExecutionPolicy | None = None
    """Resolved backend execution limits applied to this attempt."""
    backend_outcome: str | None = None
    """The backend's classification of this turn when it reported a fault (a
    ``BackendOutcome`` value); ``None`` on a clean turn or from a backend that does
    not classify.

    Recorded whatever this attempt's own ``outcome`` says, because the two can
    disagree: a turn whose output was accepted before its process faulted looks
    clean everywhere else. An exit code collapses every distinct fault onto ``1``,
    so without this an investigation cannot tell a rate limit from a dropped
    transport."""
    submission_status: str | None = None
    submissions: list[dict] = field(default_factory=list)
    """Engine-transport calls, without submitted payloads."""
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AgentRunOutcome:
    """Result of one agent's backend execution and optional output submission."""

    agent: str
    response: str
    valid: bool
    gate: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    """Run-level advisory notices (not per-attempt), each a ``{kind, message}``
    mapping built by ``run_notice``: ``session_adopt`` / ``abort``. Per-attempt
    failure diagnostics are NOT duplicated here — they live structured in
    ``attempts_detail[*].errors``; this list carries only whole-run decisions/status
    that have no per-attempt home."""
    attempts: int = 0
    submission_status: str | None = None
    last_error: str = ""
    model: str = ""
    """The model every attempt asked for (the graph's ``model:``)."""
    observed_model: str = ""
    """The model the runtime actually billed, unioned across attempts. Empty when
    the backend reported none (e.g. the mock runner)."""
    tool_calls_total: int = 0
    tools_used: list[str] = field(default_factory=list)
    """Union of tool names invoked across all attempts (P-03 telemetry)."""
    wall_clock_ms_total: float = 0.0
    """Sum of every attempt's subprocess wall-clock time in milliseconds — the
    real end-to-end cost of this agent (model + tool + MCP time), distinct from
    the API-only ``usage.duration_ms``."""
    mcp_servers: list[dict] = field(default_factory=list)
    """MCP servers loaded across attempts ({name,status,transport}).
    telemetry — empty when no MCP server was wired into the subprocess."""
    usage: AgentUsage = EMPTY_USAGE
    """Token/cost facts summed across backend attempts. ``EMPTY_USAGE`` when the run
    produced no measurable usage."""
    first_prompt: str = ""
    """The attempt-1 payload sent to the model. Schema contracts live in the system
    prompt; schema-less agents receive only their assembled context."""
    attempts_detail: list[AttemptDetail] = field(default_factory=list)
    """Per-attempt observability: one entry per backend turn. Invariant:
    ``merge_usage(d.usage for d in attempts_detail)``
    equals ``usage`` (per-attempt usages sum to the aggregate)."""
