"""result_access: the one sanctioned way to read an agent's response.

Two untyped shapes for "session results" flow through the pipeline:

  * ``dict[str, AgentRunOutcome]`` — the live review path (objects with a
    ``.response`` attribute), and
  * ``dict[str, {"response": str}]`` — the ``raw_results.json`` shape consumed
    by report-render, publish, view, and tests.

Consumers used to guess independently (some via ``getattr``, some via
``.get``), and any consumer that handled only one shape silently dropped the
other — the root cause of the empty-verdict regression. ``response_of`` is the
single accessor: every response read goes through it, so a new consumer cannot
re-arm that trap. ``to_response_map`` normalizes a whole session to the
``raw_results.json`` shape (used to re-emit that artifact).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from roundtable.backend import BackendOutcome

RUNTIME_FAILURE_OUTCOME = "api_error"
"""``AttemptDetail.outcome`` for an attempt that produced no usable backend turn —
the subprocess timed out or the SDK run failed. Set by the run loop's
``_handle_run_failure``, which also stops retrying, so this is always terminal."""

TIMEOUT_ERROR = BackendOutcome.TIMEOUT.value
"""``AgentRunOutcome.last_error`` when the attempt exhausted its ``timeout_seconds``
budget. The one runtime failure an operator fixes by raising a budget rather than
by investigating the agent. Aliased to the backend's own classification so the two
cannot drift: every other cause reports its ``BackendOutcome`` label verbatim, and
this reader distinguishes them by comparing against that same vocabulary."""


def response_of(result: Any) -> str | None:
    """Return ``result``'s response string, or ``None`` if absent/empty.

    Accepts a ``{"response": str}`` mapping (raw_results shape) or any object
    exposing a ``.response`` attribute (``AgentRunOutcome``). A missing,
    non-string, or empty response yields ``None``.
    """
    if result is None:
        return None
    resp = (
        result.get("response") if isinstance(result, Mapping) else getattr(result, "response", None)
    )
    return resp if isinstance(resp, str) and resp else None


def unreadable_reason(result: Any, *, agent: str) -> str | None:
    """Why ``agent`` produced nothing readable, or ``None`` when it did.

    ``response_of`` collapses three very different outcomes onto one ``None``, and a
    consumer that reports them as "no output" tells an operator the same story for a
    node that never ran and a node that consumed its whole retry budget and failed
    validation. The second cost real money and means the run is *unusable*, not merely
    incomplete — so it has to be sayable.

    The states come from ``AgentRunOutcome``, not from any agent's output shape, so
    this is engine-generic: every config's terminal reader needs the same distinction.
    Reads defensively, because the ``raw_results.json`` replay shape carries only
    ``response``.
    """
    if result is None:
        return f"{agent} did not run."
    if _field(result, "valid") is False:
        return f"{agent} ran but produced no valid output ({_failure_detail(result)})."
    if response_of(result) is None:
        return f"{agent} produced an empty response."
    return None


def _field(result: Any, snake: str, camel: str | None = None) -> Any:
    """Read one field from either shape this module serves.

    The live path carries ``AgentRunOutcome`` objects (snake_case attributes); every
    replay path carries the persisted JSON (camelCase keys). A reader that handles one
    and silently defaults on the other is exactly the trap this module exists to close
    — and it had already re-opened here, where the failure detail was attribute-only
    and so came out empty for every publish, which replays from disk by design.
    """
    if isinstance(result, Mapping):
        for key in (snake, camel or snake):
            if key in result:
                return result[key]
        return None
    return getattr(result, snake, None)


def _failure_detail(result: Any) -> str:
    """Why the run produced nothing: the true cause, the attempt count, diagnostics.

    The cause is read from the terminating attempt rather than assumed. An outcome
    that never reached validation — the subprocess timed out or the API failed —
    carries ``gate=None`` and no diagnostics, so describing it as a validation
    failure would send an operator to the agent's output contract to debug a problem
    that lives in its time budget. Those are opposite fixes.
    """
    attempts = _field(result, "attempts") or 0
    last_error = _last_error(result)
    parts = [_failure_cause(result, last_error)]
    if attempts:
        parts.append(f"after {attempts} attempt{'s' if attempts != 1 else ''}")
    detail = "; ".join(parts)
    return f"{detail}: {last_error}" if last_error else detail


def _last_error(result: Any) -> str:
    """The terminating failure message.

    ``AgentRunOutcome.last_error`` when present; otherwise the terminating attempt's
    first diagnostic, which is where the persisted trace keeps the same fact.
    """
    stated = (_field(result, "last_error", "lastError") or "").strip()
    if stated:
        return stated
    errors = _field(_last_attempt(result), "errors") or []
    first = errors[0] if errors else None
    return str(_field(first, "message") or "").strip() if first is not None else ""


def _failure_cause(result: Any, last_error: str) -> str:
    """The terminating attempt's cause, in an operator's words."""
    if _field(_last_attempt(result), "outcome") == RUNTIME_FAILURE_OUTCOME:
        return "timed out" if last_error == TIMEOUT_ERROR else "the model run failed"
    gate = _field(result, "gate")
    return f"failed the {gate} gate" if gate else "failed output validation"


def _last_attempt(result: Any) -> Any:
    """The terminating attempt record, or ``None`` for a shape carrying none.

    Absent from the ``raw_results.json`` replay shape, so this is read defensively.
    """
    details = _field(result, "attempts_detail", "attemptsDetail") or []
    return details[-1] if details else None


def to_response_map(session_results: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Normalize a session to the ``raw_results.json`` shape.

    Preserves insertion order and emits ``{agent: {"response": str}}`` for every
    agent, using ``""`` for absent/empty responses.
    """
    return {k: {"response": response_of(v) or ""} for k, v in session_results.items()}
