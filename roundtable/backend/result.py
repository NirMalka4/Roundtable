"""result: the backend-neutral run outcome contract.

``RunResult`` is what every backend returns — the SDK runner
(:mod:`roundtable.backend.sdk_runner`) and the ``--simulate`` mock
(:mod:`roundtable.backend.mock_runner`) both map into it, and the run loop /
scheduler consume it. It lives here (not in a backend module) so the contract is
independent of any single backend.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .outcome import BackendOutcome
from .usage import EMPTY_USAGE, AgentUsage


@dataclass(frozen=True)
class RetentionPolicy:
    max_scalar_length: int = 500
    max_tool_calls: int = 200
    max_events: int = 500


DEFAULT_RETENTION_POLICY = RetentionPolicy()

SUBMISSION_TOOL_NAME = "roundtable_submit_output"
_UNSET = object()
_PARSER_LOCATION = re.compile(
    r"^(.*? at line \d+, column \d+, character \d+\.)",
    re.DOTALL,
)
_UNMATCHED_CONTAINER = re.compile(r"Unmatched .*? opened at line \d+, column \d+, character \d+\.")


@dataclass(frozen=True)
class SubmissionValidation:
    """Backend-neutral projection of one full OVG validation result."""

    passed: bool
    diagnostics: tuple[dict[str, str], ...] = ()
    warnings: tuple[dict[str, str], ...] = ()
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionAttempt:
    """Metadata for one in-turn submission call; the submitted payload is excluded."""

    status: str
    correction: int
    diagnostic_fingerprints: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, str], ...] = ()
    warnings: tuple[dict[str, str], ...] = ()
    feedback: str = ""
    retained_feedback: str = ""
    repeated: bool = False
    terminal_completion: bool = False

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "status": self.status,
            "correction": self.correction,
            "terminalCompletion": self.terminal_completion,
        }
        if self.diagnostic_fingerprints:
            item["diagnosticFingerprint"] = self.diagnostic_fingerprints[0]
        if self.repeated:
            item["repeated"] = True
        return item

    def retained_diagnostics(self) -> tuple[dict[str, str], ...]:
        return retain_submission_diagnostics(self.diagnostics)

    def retained_warnings(self) -> tuple[dict[str, str], ...]:
        return retain_submission_diagnostics(self.warnings)


def _bounded_detail(value: Any) -> str:
    text = str(value)
    limit = DEFAULT_RETENTION_POLICY.max_scalar_length
    if len(text) <= limit:
        return text
    marker = "…[truncated]…"
    retained = limit - len(marker)
    head = retained // 2
    tail = retained - head
    return f"{text[:head]}{marker}{text[-tail:]}"


def _retained_message(diagnostic: dict[str, str]) -> str:
    message = str(diagnostic.get("message", "validation failed"))
    if diagnostic.get("gate") == "format":
        location = _PARSER_LOCATION.search(message)
        unmatched = _UNMATCHED_CONTAINER.search(message)
        details = [
            match.group(1) if match is location else match.group(0)
            for match in (location, unmatched)
            if match is not None
        ]
        if details:
            return " ".join(details)
        return "invalid JSON; see the retained parser location"

    keyword = diagnostic.get("keyword", "")
    safe_schema_messages = {
        "required": "a required property is missing",
        "type": "value has the wrong JSON type",
        "enum": "value is not in the allowed set",
        "const": "value does not equal the required constant",
        "not": "value is not allowed by the schema",
        "minItems": "array has too few items",
        "maxItems": "array has too many items",
        "contains": "array does not contain the required item shape",
        "additionalProperties": "object contains a property not allowed by the schema",
        "propertyNames": "object contains a property name not allowed by the schema",
    }
    if keyword:
        return safe_schema_messages.get(keyword, f"value failed schema keyword {keyword!r}")
    kind = diagnostic.get("kind")
    if kind:
        return f"backend diagnostic {kind!r}"
    gate = str(diagnostic.get("gate") or "validation")
    return f"output failed gate {gate!r}"


def _retain_submission_diagnostic(diagnostic: dict[str, str]) -> dict[str, str]:
    retained: dict[str, str] = {}
    for key in ("kind", "gate", "path", "keyword", "expected", "description", "location"):
        value = diagnostic.get(key)
        if value:
            retained[key] = _bounded_detail(value)
    retained["message"] = _bounded_detail(_retained_message(diagnostic))
    return retained


def retain_submission_diagnostics(
    diagnostics: tuple[dict[str, str], ...] | list[dict[str, str]],
) -> tuple[dict[str, str], ...]:
    """Project model-facing validation details to the artifact retention contract."""

    return tuple(_retain_submission_diagnostic(d) for d in diagnostics)


@dataclass(frozen=True)
class SubmissionResult:
    """Snapshot of a submission contract after one backend turn."""

    status: str
    attempts: tuple[SubmissionAttempt, ...] = ()
    accepted_output: Any = None

    @property
    def accepted(self) -> bool:
        """Did the transport take a payload that satisfies the output contract?

        Acceptance is the terminal fact of a schema-backed turn: once it holds, the
        canonical output exists and nothing the backend process does afterwards can
        unmake it.
        """
        return self.status == "accepted"

    @property
    def correction_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.status == "rejected")

    @property
    def terminal_completion(self) -> bool:
        return bool(self.attempts and self.attempts[-1].terminal_completion)


def _diagnostic_fingerprint(diagnostic: dict[str, str]) -> str:
    stable = "\x1f".join(
        str(diagnostic.get(key, "")) for key in ("gate", "path", "keyword", "location", "message")
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _diagnostic_line(diagnostic: dict[str, str]) -> str:
    gate = diagnostic.get("gate", "validation")
    path = diagnostic.get("path") or "<root>"
    keyword = diagnostic.get("keyword")
    label = f"[{gate}] {path}"
    if keyword:
        label += f" ({keyword})"
    line = f"{label}: {diagnostic.get('message', 'validation failed')}"
    description = diagnostic.get("description")
    if description:
        line += f" Field contract: {description}"
    return line


def _render_submission_feedback(
    diagnostics: tuple[dict[str, str], ...],
    hints: tuple[str, ...],
    repeated: bool,
) -> str:
    heading = (
        "OUTPUT REJECTED — the previous correction was not applied; "
        "the validation defect is unchanged:"
        if repeated
        else "OUTPUT REJECTED — correct these exact validation defects:"
    )
    lines = [heading, *[f"- {_diagnostic_line(d)}" for d in diagnostics]]
    if hints:
        lines.extend(("", "Gate guidance:", *hints))
    lines.extend(
        (
            "",
            f"Correct the object and call {SUBMISSION_TOOL_NAME} again in this same turn.",
            "Do not print or duplicate the JSON in assistant text.",
        )
    )
    return "\n".join(lines)


def render_submission_feedback(
    validation: SubmissionValidation,
    repeated_fingerprints: set[str],
) -> tuple[str, str, tuple[str, ...], bool]:
    """Render model-facing feedback and its separately redacted artifact projection."""

    retained_diagnostics = retain_submission_diagnostics(validation.diagnostics)
    fingerprints = tuple(_diagnostic_fingerprint(d) for d in retained_diagnostics)
    repeated = bool(fingerprints and all(fp in repeated_fingerprints for fp in fingerprints))
    feedback = _render_submission_feedback(validation.diagnostics, validation.hints, repeated)
    retained_feedback = _render_submission_feedback(
        retained_diagnostics,
        validation.hints,
        repeated,
    )
    return feedback, retained_feedback, fingerprints, repeated


@dataclass
class OutputSubmission:
    """Session-scoped engine contract implemented by each backend."""

    parameters: dict[str, Any]
    validator: Callable[[Any, list[dict[str, Any]]], SubmissionValidation]
    seen_fingerprints: set[str] = field(default_factory=set)
    tool_output_names: frozenset[str] = field(default_factory=frozenset)
    accepted_output: Any = field(default=_UNSET, init=False, repr=False)
    attempts: list[SubmissionAttempt] = field(default_factory=list, init=False)

    def reject_arguments(self, message: str) -> SubmissionAttempt:
        validation = SubmissionValidation(
            passed=False,
            diagnostics=(
                {
                    "gate": "submission",
                    "path": "output",
                    "keyword": "required",
                    "message": message,
                },
            ),
        )
        return self._record_rejection(validation)

    def submit(
        self,
        output: Any,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> SubmissionAttempt:
        validation = self.validator(output, list(tool_calls or ()))
        if validation.passed:
            self.accepted_output = output
            accepted = SubmissionAttempt(
                status="accepted",
                correction=sum(1 for a in self.attempts if a.status == "rejected"),
                warnings=validation.warnings,
                terminal_completion=True,
            )
            self.attempts.append(accepted)
            return accepted
        return self._record_rejection(validation)

    def _record_rejection(self, validation: SubmissionValidation) -> SubmissionAttempt:
        feedback, retained_feedback, fingerprints, repeated = render_submission_feedback(
            validation, self.seen_fingerprints
        )
        self.seen_fingerprints.update(fingerprints)
        attempt = SubmissionAttempt(
            status="rejected",
            correction=sum(1 for a in self.attempts if a.status == "rejected") + 1,
            diagnostic_fingerprints=fingerprints,
            diagnostics=validation.diagnostics,
            warnings=validation.warnings,
            feedback=feedback,
            retained_feedback=retained_feedback,
            repeated=repeated,
        )
        self.attempts.append(attempt)
        return attempt

    def snapshot(self) -> SubmissionResult:
        if self.accepted_output is not _UNSET:
            status = "accepted"
            accepted = self.accepted_output
        elif self.attempts:
            status = "rejected"
            accepted = None
        else:
            status = "missing"
            accepted = None
        return SubmissionResult(
            status=status, attempts=tuple(self.attempts), accepted_output=accepted
        )

    def canonical_output(self) -> str:
        if self.accepted_output is _UNSET:
            return ""
        return json.dumps(self.accepted_output, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ExecutionPolicy:
    """Resolved backend execution limits applied to one agent attempt."""

    shell_invocation_cap_s: float | None = None
    background_poll_cap_s: float | None = None
    detached_allowed: bool | None = None

    def to_dict(self) -> dict[str, float | bool]:
        values = (
            ("shellInvocationCapSeconds", self.shell_invocation_cap_s),
            ("backgroundPollCapSeconds", self.background_poll_cap_s),
            ("detachedAllowed", self.detached_allowed),
        )
        return {key: value for key, value in values if value is not None}


@dataclass(frozen=True)
class RunRequest:
    agent: str
    prompt: str
    add_dirs: tuple[str, ...] = ()
    timeout_s: float = 600.0
    session_id: str | None = None
    cwd: str | None = None
    submission: OutputSubmission | None = None
    validation_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validation_context",
            MappingProxyType(dict(self.validation_context)),
        )


@dataclass
class RunResult:
    """Parsed outcome of one agent run (any backend)."""

    final_content: str
    """Content of the last assistant message with non-empty content."""

    tool_call_count: int
    """Total tool calls requested across all assistant messages."""

    rounds: int
    """Number of assistant messages (post-hoc round-budget proxy)."""

    exit_code: int | None
    """Terminal exit code mapped from the backend outcome (None if absent)."""

    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    """Names of every tool the agent invoked, in request order (may repeat).

    Telemetry for tool-usage reporting (usage summary + HTML report). Empty when
    no recoverable tool names are exposed."""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    """Every tool call as ``{name, args, ok?, toolError?}`` in request order.

    ``toolError`` carries SDK-supplied structured provenance. Redaction and safe
    field selection happen at the persistence boundary.
    """
    events: list[dict[str, Any]] = field(default_factory=list)
    events_truncated: bool = False
    events_omitted_count: int = 0
    timed_out: bool = False
    timeout_phase: str | None = None
    """Where a timeout occurred: model_wait/tool_running/background_tool_wait/cleanup."""
    timeout_snapshot: list[dict[str, Any]] = field(default_factory=list)
    """Safe active tool/background-process facts captured before timeout cleanup."""
    wall_clock_s: float = 0.0
    raw_stdout: str = ""
    raw_stderr: str = ""
    backend_outcome: BackendOutcome | None = None
    """Structured outcome classified by the backend (``None`` for an unclassified
    mock/backend), avoiding downstream re-parsing of ``raw_stderr``."""
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    """MCP servers loaded for this run ({name, status, transport}).
    telemetry for the live-acceptance check. Empty when no MCP server was wired."""
    token_usage: AgentUsage = EMPTY_USAGE
    """Token/cost facts derived from the backend usage stream. ``EMPTY_USAGE``
    when the run produced no measurable usage (e.g. a no-op or failed run)."""
    execution_policy: ExecutionPolicy | None = None
    """Resolved backend execution limits actually applied to this run."""
    submission: SubmissionResult | None = None
    """Submission metadata kept separate from ordinary tool-call telemetry."""

    @property
    def ok(self) -> bool:
        has_output = self.submission is not None or bool(self.final_content)
        return not self.timed_out and self.exit_code == 0 and has_output


# Compatibility alias retained for callers written before the backend contract
# became canonical.
CopilotResult = RunResult
