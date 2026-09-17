"""Agent run loop: terminal submission for schemas, raw output otherwise.

One agent is run up to ``DEFAULT_MAX_ATTEMPTS`` times. Schema-backed agents must
successfully call the engine-owned terminal submission tool; its handler applies the
configured output-validation gates in-turn. Schema-less/unknown agents have no structured-output
contract, so their first successful backend response is forwarded unchanged.

The loop-carried state is held in ``_RunState`` and the loop body reads as a short
sequence of named steps (``_begin_attempt`` → ``_invoke`` → ``_record_attempt`` →
``_handle_run_failure`` / ``_handle_ovg`` → ``_finalize``), each a single concern.

Scope notes:
  - Tool-budget-exhaustion recovery and the abort grace-timer machinery are
    intentionally omitted (the plan drops grace/force-fail for the MVP).

The LLM call is injected (``backend``) so the loop is unit-testable without a live
backend. The CLI supplies the SDK-backed ``backend`` (:mod:`runtime.sdk_runner`);
tests inject fakes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from roundtable.backend import (
    DEFAULT_RETENTION_POLICY,
    EMPTY_USAGE,
    NOT_APPLICABLE_BILLING,
    AgentUsage,
    Backend,
    BackendOutcome,
    BillingValue,
    OutputSubmission,
    RunRequest,
    RunResult,
    SubmissionValidation,
    normalize_backend_result,
    retain_submission_diagnostics,
)
from roundtable.graph import Configuration
from roundtable.persistence import retain_tool_calls

# The schema-submission attempt budget is configurable; model choice is a scalar graph-owned
# custom-agent property and never changes inside this loop.
from roundtable.settings import DEFAULT_MAX_ATTEMPTS
from roundtable.validation import (
    build_submission_schema,
    evaluate_agent_output,
    evaluate_agent_value,
    load_hint,
)

from ...result_access import RUNTIME_FAILURE_OUTCOME, TIMEOUT_ERROR
from .diagnostics import run_notice, runtime_error, structured_diags
from .model import AgentRunOutcome, AttemptDetail
from .retry_feedback import strip_preamble, truncate_for_echo

# Wall-clock budget for ONE attempt when the graph entry declares no ``timeout_seconds``.
# A breach is terminal (``_handle_run_failure``): the attempt's partial work is
# unrecoverable, so this is the point past which an agent's output is written off.
DEFAULT_AGENT_TIMEOUT_S = 600.0


def _log(msg: str) -> None:
    print(f"[agent_runner] {msg}", file=sys.stderr)


def _process_finished_cleanly(result: RunResult) -> bool:
    """Did the backend process end without timing out or reporting a failure?"""
    return not result.timed_out and result.exit_code == 0


def _backend_fault_label(result: RunResult) -> str:
    """This turn's classification when the backend reported a fault, else ``""``.

    A clean success is the norm and carries no information; recording only the
    exceptions keeps every consumer to one rule — if a label is present, something
    went wrong on this turn, whatever the turn's own outcome says.
    """
    outcome = getattr(result, "backend_outcome", None)
    if not isinstance(outcome, BackendOutcome) or outcome is BackendOutcome.SUCCESS:
        return ""
    return outcome.value


def _backend_failure_message(result: RunResult) -> str:
    """The backend's diagnostic text, reduced to its first line and length-capped."""
    text = (getattr(result, "raw_stderr", "") or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    limit = DEFAULT_RETENTION_POLICY.max_scalar_length
    return first_line if len(first_line) <= limit else first_line[:limit] + "…"


def _backend_failure_reason(result: RunResult) -> str:
    """Why this turn ended badly, named by the backend that classified it.

    The exit code collapses every distinct fault onto ``1``, so reporting it would
    leave an investigation with nothing to act on. A timeout keeps the wording
    downstream readers match against; every other cause reports its classification
    and the backend's own message.
    """
    if result.timed_out:
        return TIMEOUT_ERROR
    label = _backend_fault_label(result) or BackendOutcome.UNKNOWN.value
    message = _backend_failure_message(result)
    return f"{label}: {message}" if message else label


@dataclass
class _RunState:
    """Loop-carried state for one ``run_agent_with_ovg`` invocation.

    Config fields (``agent`` … ``session_reuse``) are the immutable inputs; the
    remaining fields are the mutating loop state. The step methods on this object
    each own one concern of the retry loop and mutate ``self`` in place; ``run``
    drives them and returns the aggregate ``AgentRunOutcome``.
    """

    agent: str
    context: str
    backend: Backend
    configuration: Configuration
    model: str
    add_dirs: list[str] | None
    timeout_s: float
    max_attempts: int
    session_reuse: bool
    cwd: str | None = None
    # Caller-owned facts available to validation gates.
    ovg_context: dict[str, Any] | None = None

    # --- computed once (from config) ---
    # NOTE: the schema-derived output CONTRACT now lives in the agent's system prompt
    # (``config.agent_setup.full_system_prompt`` → ``## Output contract``); the context
    # payload carries only the run context + attempt-specific gate-error feedback.

    # --- aggregate loop state ---
    full_response: str = ""
    attempts: int = 0
    validation_attempts: int = 0
    model_wait_retry_used: bool = False
    is_valid: bool = False
    last_error: str = ""
    last_errors: list[str] = field(default_factory=list)
    final_gate: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    total_tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    total_wall_clock_ms: float = 0.0
    mcp_servers_by_name: dict[str, dict] = field(default_factory=dict)
    first_prompt: str = ""
    usage_total: AgentUsage = EMPTY_USAGE
    attempts_detail: list[AttemptDetail] = field(default_factory=list)
    submission_fingerprints: set[str] = field(default_factory=set)
    last_retained_retry_feedback: str = ""

    # --- session-reuse state ---
    # ``session_id`` is the id of the current session; ``session_is_fresh`` is True on
    # the first turn of a session (full context) and False on a resumed turn
    # (feedback-only). ``resume_next`` is set by the prior attempt's terminating
    # branch: a rejected schema submission resumes the same session. ``session_*_seen`` track
    # the running session-cumulative ``result.usage`` fields so a resumed turn
    # contributes only its delta (billing / duration are session totals, not
    # per-turn, so summing them would double-count).
    session_id: str = ""
    session_is_fresh: bool = True
    resume_next: bool = False
    session_billing_seen: BillingValue = NOT_APPLICABLE_BILLING
    session_duration_seen: float = 0.0
    session_walltime_seen: float = 0.0

    # --- per-attempt transients ---
    resume: bool = False
    current_retry_feedback: str = ""
    current_retained_retry_feedback: str = ""
    current_detail: AttemptDetail | None = None

    def run(self) -> AgentRunOutcome:
        while self.validation_attempts < self.max_attempts and not self.is_valid:
            payload = self._begin_attempt()
            result = self._invoke(payload)
            self._record_attempt(result)
            if not self._backend_turn_succeeded(result):
                if self._handle_run_failure(result):
                    break
                continue
            if self._handle_output(result):
                break
        return self._finalize()

    def _backend_turn_succeeded(self, result: RunResult) -> bool:
        """Did this turn produce something the loop can act on?

        An accepted submission is itself the answer: the engine transport already
        validated the payload and holds it, so the turn stands however the backend
        process ended afterwards. Short of acceptance the loop needs a cleanly
        finished process — for a submission so the rejection can steer a retry, and
        otherwise so the raw content can be trusted.
        """
        submission = getattr(result, "submission", None)
        if submission is not None:
            return submission.accepted or _process_finished_cleanly(result)
        return result.ok

    def _begin_attempt(self) -> str:
        """Advance the attempt counter, select the session, and build the payload."""
        self.attempts += 1

        self.resume = self.session_reuse and self.attempts > 1 and self.resume_next
        if self.session_reuse:
            if self.resume:
                self.session_is_fresh = False  # keep the existing session_id, resume it
            else:
                self.session_id = str(uuid4())  # fresh session for attempt 1
                self.session_is_fresh = True
                self.session_billing_seen = NOT_APPLICABLE_BILLING
                self.session_duration_seen = 0.0
                self.session_walltime_seen = 0.0

        if self.validation_attempts > 0:
            self.current_retry_feedback = self.last_errors[0] if self.last_errors else ""
            self.current_retained_retry_feedback = self.last_retained_retry_feedback
        else:
            self.current_retry_feedback = ""
            self.current_retained_retry_feedback = self.current_retry_feedback
        payload = self._build_payload()
        if self.attempts == 1:
            self.first_prompt = payload
        return payload

    def _build_payload(self) -> str:
        """The prompt for this attempt: fresh full context, a resumed feedback-only turn,
        or the legacy full-context-plus-echo retry (``--no-session-reuse``).

        The stable output contract is in the system prompt (``## Output contract``), so a
        payload carries only the run context and attempt-specific gate-error feedback."""
        retry_feedback = self.current_retry_feedback
        if self.resume:
            # Guard: a blank constraint (attempts beyond the schedule) would give a
            # resumed turn nothing to act on — fall back to the full context.
            return retry_feedback if retry_feedback.strip() else self.context
        payload = self.context
        if self.current_retry_feedback:
            payload += (
                "\n\n---\nPREVIOUS ATTEMPT OUTPUT:\n"
                + truncate_for_echo(self.full_response)
                + "\n\n---\n"
                + retry_feedback
            )
        return payload

    def _invoke(self, payload: str) -> RunResult:
        """Run one attempt through ``backend`` with the resolved subprocess kwargs."""
        _log(f"{self.agent}: attempt {self.attempts}/{self.max_attempts} (model={self.model})")
        submission = self._build_submission()
        request = RunRequest(
            agent=self.agent,
            prompt=payload,
            add_dirs=tuple(self.add_dirs or ()),
            timeout_s=self.timeout_s,
            session_id=self.session_id if self.session_reuse and self.session_id else None,
            cwd=self.cwd,
            submission=submission,
            validation_context=self.ovg_context or {},
        )
        return normalize_backend_result(request, self.backend.run(request))

    @property
    def _schema_backed(self) -> bool:
        entry = self.configuration.by_key.get(self.agent)
        return bool(entry is not None and entry.is_llm and entry.output_schema is not None)

    def _build_submission(self) -> OutputSubmission | None:
        entry = self.configuration.by_key.get(self.agent)
        if entry is None or not entry.is_llm or entry.output_schema is None:
            return None

        parameters = build_submission_schema(
            entry.output_schema,
            self.configuration.root / "schemas",
        )

        def validate(value: Any, tool_calls: list[dict[str, Any]]) -> SubmissionValidation:
            result = evaluate_agent_value(
                self.agent,
                value,
                {
                    "tool_calls": tool_calls,
                    **(self.ovg_context or {}),
                },
                configuration=self.configuration,
            )
            errors = tuple(structured_diags(result.errors, result.parsed, ""))
            warnings = tuple(structured_diags(result.warnings, result.parsed, ""))
            hints: list[str] = []
            for name in result.failed_hints:
                try:
                    hint = load_hint(name, self.configuration.root / "hints").strip()
                except Exception:
                    continue
                if hint:
                    hints.append(hint)
            return SubmissionValidation(result.passed, errors, warnings, tuple(hints))

        output_tools = frozenset(
            tool
            for gate in entry.ovg_gates or ()
            if isinstance(gate, dict)
            for params in (gate.get("params"),)
            if isinstance(params, dict)
            for tool in params.get("tool_output_names", ())
            if isinstance(tool, str) and tool
        )
        return OutputSubmission(
            parameters,
            validate,
            self.submission_fingerprints,
            tool_output_names=output_tools,
        )

    def _record_attempt(self, result: RunResult) -> None:
        """Accumulate telemetry (tools, usage, MCP servers) and open this attempt's detail."""
        attempt_tool_calls = getattr(result, "tool_call_count", 0) or 0
        self.total_tool_calls += attempt_tool_calls
        attempt_tools = [
            t for t in (getattr(result, "tools_used", None) or []) if isinstance(t, str)
        ]
        self.tools_used.extend(attempt_tools)
        attempt_raw_calls = [
            c for c in (getattr(result, "tool_calls", None) or []) if isinstance(c, dict)
        ]
        retained_calls = retain_tool_calls(attempt_raw_calls)
        attempt_wall_ms = float(getattr(result, "wall_clock_s", 0.0) or 0.0) * 1000.0
        self.total_wall_clock_ms += attempt_wall_ms
        self._adopt_reported_session(result)
        attempt_usage = self._merge_attempt_usage(result)
        self.current_detail = AttemptDetail(
            attempt=self.attempts,
            model=self.model,
            observed_model=", ".join((attempt_usage or EMPTY_USAGE).observed_models),
            outcome="",  # resolved by the branch that terminates this attempt
            retry_feedback=self.current_retained_retry_feedback,
            tools_invoked=attempt_tools,
            tool_calls=retained_calls.calls,
            tool_calls_truncated=retained_calls.truncated,
            tool_calls_omitted_count=retained_calls.omitted_count,
            events=list(getattr(result, "events", None) or []),
            events_truncated=bool(getattr(result, "events_truncated", False)),
            events_omitted_count=int(getattr(result, "events_omitted_count", 0) or 0),
            wall_clock_ms=attempt_wall_ms,
            usage=attempt_usage if attempt_usage is not None else EMPTY_USAGE,
            timeout_phase=getattr(result, "timeout_phase", None),
            timeout_snapshot=[
                dict(item)
                for item in (getattr(result, "timeout_snapshot", None) or [])
                if isinstance(item, dict)
            ],
            execution_policy=getattr(result, "execution_policy", None),
            backend_outcome=_backend_fault_label(result) or None,
            submission_status=(
                getattr(getattr(result, "submission", None), "status", None)
                if self._schema_backed
                else None
            ),
            warnings=self._billing_warnings(result, attempt_usage),
            submissions=[
                attempt.to_dict()
                for attempt in (
                    getattr(getattr(result, "submission", None), "attempts", None) or ()
                )
            ],
        )
        self.attempts_detail.append(self.current_detail)
        for srv in getattr(result, "mcp_servers", None) or []:
            if isinstance(srv, dict) and isinstance(srv.get("name"), str):
                self.mcp_servers_by_name[srv["name"]] = srv

    def _adopt_reported_session(self, result: RunResult) -> None:
        """Adopt the session id the CLI actually reports so a subsequent resume
        targets the real session even if the CLI ever diverged from the requested id
        (observed to echo it exactly, but adopt defensively). A mismatch is logged;
        adopting is strictly safer than asserting-and-failing."""
        if not (self.session_reuse and self.session_id):
            return
        reported = getattr(result, "session_id", None)
        if reported and reported != self.session_id:
            self.warnings.append(
                run_notice(
                    "session_adopt",
                    f"Attempt {self.attempts}: CLI returned session id {reported} "
                    f"(requested {self.session_id}); adopting reported id for resume.",
                )
            )
            self.session_id = reported

    def _billing_warnings(
        self,
        result: RunResult,
        usage: AgentUsage | None,
    ) -> list[dict[str, str]]:
        billing = usage.billing if usage is not None else NOT_APPLICABLE_BILLING
        if not billing.is_unavailable:
            return []
        session_id = getattr(result, "session_id", None) or self.session_id
        return [
            {
                "kind": billing.warning_code or "sdk_usage_metrics_unavailable",
                "source": billing.source,
                "status": billing.status.value,
                "sessionId": str(session_id),
                "message": "SDK usage metrics unavailable; AI Credit reporting omitted.",
            }
        ]

    def _merge_attempt_usage(self, result: RunResult) -> AgentUsage | None:
        """Merge this attempt's usage into the aggregate, contributing only the delta on a
        resumed turn (``result.usage`` fields are session-cumulative there). Returns the
        per-attempt usage recorded on the detail, or ``None`` when the runner reported none."""
        attempt_usage = getattr(result, "token_usage", None)
        if attempt_usage is None:
            return None
        merge_usage = attempt_usage
        if self.session_reuse and not self.session_is_fresh:
            merge_usage = replace(
                attempt_usage,
                billing=attempt_usage.billing.delta(self.session_billing_seen),
                duration_ms=max(0.0, attempt_usage.duration_ms - self.session_duration_seen),
                session_duration_ms=max(
                    0.0, attempt_usage.session_duration_ms - self.session_walltime_seen
                ),
            )
        self.session_billing_seen = attempt_usage.billing
        self.session_duration_seen = attempt_usage.duration_ms
        self.session_walltime_seen = attempt_usage.session_duration_ms
        self.usage_total = self.usage_total.merge(merge_usage)
        return merge_usage

    def _handle_run_failure(self, result: RunResult) -> bool:
        """Record a non-ok SDK result; retry one model-wait timeout in a fresh session."""
        phase = getattr(result, "timeout_phase", None)
        self.last_error = _backend_failure_reason(result)
        assert self.current_detail is not None
        self.current_detail.outcome = RUNTIME_FAILURE_OUTCOME
        detail = (
            f"{self.last_error} (phase={phase})" if result.timed_out and phase else self.last_error
        )
        self.current_detail.errors = [runtime_error(self.current_detail.outcome, detail)]
        if result.timed_out and phase == "model_wait" and not self.model_wait_retry_used:
            self.model_wait_retry_used = True
            self.resume_next = False
            _log(f"{self.agent}: model-wait timeout — retrying once in a fresh session")
            return False
        _log(
            f"{self.agent}: SDK run failed on model {self.model} — {detail} "
            f"(exit={result.exit_code}) — stopping retries"
        )
        return True

    def _note_output_survived_backend_failure(self, result: RunResult) -> None:
        """Keep a faulting backend process visible once its output was accepted.

        The submission stands, so nothing downstream has cause to report a problem —
        which is precisely why the fault has to be recorded here, or the next
        investigation starts from an attempt that looks clean.
        """
        if _process_finished_cleanly(result):
            return
        reason = _backend_failure_reason(result)
        _log(f"{self.agent}: output accepted, but the backend turn ended with {reason}")
        self.warnings.append(
            run_notice(
                "backend_unclean_exit",
                f"Attempt {self.attempts}: output was accepted, then the backend turn "
                f"ended with {reason}.",
            )
        )

    def _handle_output(self, result: RunResult) -> bool:
        """Accept raw schema-less output or validate a schema-backed submission."""

        self.validation_attempts += 1
        if self._schema_backed:
            return self._handle_submission(result)
        assert self.current_detail is not None
        self.full_response = result.final_content
        self.is_valid = True
        self.final_gate = None
        self.current_detail.outcome = "raw_output"
        return True

    def _handle_submission(self, result: RunResult) -> bool:
        """Fail closed unless the terminal submission tool accepted this turn."""

        assert self.current_detail is not None
        submission = getattr(result, "submission", None)
        status = getattr(submission, "status", "missing")
        self.current_detail.submission_status = status
        raw_stdout = getattr(result, "raw_stdout", None)
        raw_assistant = strip_preamble(
            raw_stdout
            if isinstance(raw_stdout, str) and raw_stdout
            else (result.final_content if status != "accepted" else "")
        )
        if status == "accepted":
            self.full_response = result.final_content
            self.is_valid = True
            self.final_gate = "all"
            self.current_detail.outcome = "submission_valid"
            final_attempt = (
                submission.attempts[-1] if submission is not None and submission.attempts else None
            )
            if final_attempt is not None:
                self.current_detail.warnings.extend(
                    dict(d) for d in final_attempt.retained_warnings()
                )
            self._note_output_survived_backend_failure(result)
            return True

        self.is_valid = False
        self.final_gate = "submission"
        self.current_detail.outcome = (
            "submission_rejected" if status == "rejected" else "submission_missing"
        )
        diagnostics: list[dict[str, str]] = []
        feedback = ""
        retained_feedback = ""
        if submission is not None and submission.attempts:
            final_attempt = submission.attempts[-1]
            diagnostics = [dict(d) for d in final_attempt.retained_diagnostics()]
            self.current_detail.warnings.extend(dict(d) for d in final_attempt.retained_warnings())
            feedback = final_attempt.feedback
            retained_feedback = final_attempt.retained_feedback

        if not diagnostics and raw_assistant:
            raw_result = evaluate_agent_output(
                self.agent,
                raw_assistant,
                {
                    "tool_calls": result.tool_calls or [],
                    **(self.ovg_context or {}),
                },
                configuration=self.configuration,
            )
            diagnostics = [
                dict(d)
                for d in retain_submission_diagnostics(
                    structured_diags(raw_result.errors, raw_result.parsed, raw_assistant)
                )
            ]

        if status == "missing":
            tool_count = int(getattr(result, "tool_call_count", 0) or 0)
            if raw_assistant:
                reason = (
                    "The turn ended with assistant text instead of a "
                    "roundtable_submit_output call. Assistant text is not accepted "
                    "for a schema-backed agent."
                )
            elif tool_count:
                reason = (
                    f"The turn ended after {tool_count} tool calls without calling "
                    "roundtable_submit_output, so no structured output was submitted "
                    "for validation."
                )
            else:
                reason = (
                    "The turn ended without calling roundtable_submit_output, so no "
                    "structured output was submitted for validation."
                )
            diagnostic_feedback = [
                str(item.get("message", "")) for item in diagnostics if item.get("message")
            ]
            if not diagnostics:
                diagnostics.append(runtime_error(self.current_detail.outcome, reason))
            feedback_lines = [
                "OUTPUT NOT SUBMITTED — the previous turn did not complete the "
                "required terminal submission.",
                reason,
            ]
            if diagnostic_feedback:
                feedback_lines.extend(("", "Assistant text diagnostics:", *diagnostic_feedback))
            feedback_lines.extend(
                (
                    "",
                    "Finish the analysis, then call roundtable_submit_output with the "
                    "required output value.",
                    "Do not print or duplicate the JSON in assistant text.",
                )
            )
            feedback = "\n".join(feedback_lines)
            retained_feedback = feedback

        if not diagnostics:
            diagnostics = [
                runtime_error(
                    self.current_detail.outcome,
                    "All roundtable_submit_output calls were rejected.",
                )
            ]
        self.current_detail.errors = diagnostics
        self.last_errors = [
            str(item.get("message", "")) for item in diagnostics if item.get("message")
        ]
        if feedback:
            self.last_errors = [feedback]
        self.last_retained_retry_feedback = (
            retained_feedback
            if retained_feedback
            else (self.last_errors[0] if self.last_errors else "")
        )
        self.last_error = "; ".join(self.last_errors)
        self.full_response = raw_assistant
        self.resume_next = True
        return False

    def _finalize(self) -> AgentRunOutcome:
        """Build the aggregate outcome after backend or submission completion."""
        if not self.is_valid:
            self.warnings.append(
                run_notice(
                    "abort",
                    f"Failed to produce valid output after {self.attempts} attempts.",
                )
            )

        return AgentRunOutcome(
            agent=self.agent,
            response=self.full_response if self.is_valid else "",
            valid=self.is_valid,
            gate=self.final_gate,
            errors=self.last_errors,
            warnings=self.warnings,
            attempts=self.attempts,
            submission_status=(
                self.attempts_detail[-1].submission_status
                if self._schema_backed and self.attempts_detail
                else None
            ),
            last_error=self.last_error,
            model=self.model,
            observed_model=", ".join(self.usage_total.observed_models),
            tool_calls_total=self.total_tool_calls,
            tools_used=self.tools_used,
            wall_clock_ms_total=self.total_wall_clock_ms,
            mcp_servers=list(self.mcp_servers_by_name.values()),
            first_prompt=self.first_prompt,
            usage=self.usage_total,
            attempts_detail=self.attempts_detail,
        )


def run_agent_with_ovg(
    *,
    agent: str,
    context: str,
    model: str = "",
    backend: Backend,
    configuration: Configuration,
    add_dirs: list[str] | None = None,
    timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    session_reuse: bool = True,
    cwd: str | None = None,
    ovg_context: dict[str, Any] | None = None,
) -> AgentRunOutcome:
    """Run ``agent`` with terminal submission or unvalidated schema-less text.

    ``session_reuse`` (default ``True``) controls schema-submission retry transport.
    When enabled, attempt 1 mints a fresh ``--session-id`` and sends the full
    context; a rejected schema submission *resumes that same session* with a
    feedback-only payload, so the
    model re-answers with the full diff still live in-session instead of
    re-receiving ~80 KB. When ``session_reuse`` is ``False``
    (``--no-session-reuse``) every validation attempt starts a fresh session and
    receives the full context plus the retained correction.

    """
    state = _RunState(
        agent=agent,
        context=context,
        backend=backend,
        configuration=configuration,
        model=model,
        add_dirs=add_dirs,
        timeout_s=timeout_s,
        max_attempts=max_attempts,
        session_reuse=session_reuse,
        cwd=cwd,
        ovg_context=ovg_context,
    )
    return state.run()
