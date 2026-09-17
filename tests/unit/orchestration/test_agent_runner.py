"""Unit tests for the agent_runner OVG run loop (with an injected fake runner)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import partial

import pytest
import yaml

from roundtable.backend import RunResult
from roundtable.backend.outcome import BackendOutcome
from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration
from roundtable.result_access import TIMEOUT_ERROR
from roundtable.settings.workspace import DEFAULT_MAX_ATTEMPTS
from tests.support.engine import run_agent_with_ovg as _run_agent_with_ovg

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
_SCHEMA_DIR = _CONFIG.root / "schemas"
run_agent_with_ovg = partial(_run_agent_with_ovg, configuration=_CONFIG)


@dataclass
class FakeResult:
    final_content: str
    exit_code: int | None = 0
    timed_out: bool = False
    tool_call_count: int = 0
    raw_stderr: str = ""
    session_id: str | None = None
    tools_used: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    wall_clock_s: float = 0.0
    backend_outcome: BackendOutcome | None = None
    timeout_phase: str | None = None
    timeout_snapshot: list[dict] = field(default_factory=list)
    submit_output: bool = True

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and bool(self.final_content)


def _runner(scripted):
    """Return a run_fn that yields scripted results in order, recording calls."""
    calls = []
    it = iter(scripted)

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **_kw):
        calls.append({"agent": agent, "prompt": prompt, "session_id": _kw.get("session_id")})
        result = next(it)
        submission = _kw.get("submission")
        if submission is not None and result.submit_output:
            try:
                output = json.loads(result.final_content)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                submission.submit(output, tool_calls=result.tool_calls)
        return result

    run_fn.calls = calls  # type: ignore[attr-defined]
    return run_fn


_VALID_SECURITY = json.dumps(
    {
        "findings": [
            {
                "id": "S1",
                "title": "x",
                "severity": "low",
                "description": "d",
                "locations": [{"filePath": "a.cs", "startLine": 1, "endLine": 2}],
                "impact": "i",
                "fix": "Escape the interpolated identifier before building the SQL string.",
            }
        ],
    }
)


def test_first_attempt_success():
    run_fn = _runner([FakeResult(_VALID_SECURITY)])
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert out.attempts == 1
    assert out.gate == "all"
    # Canonicalised JSON round-trips.
    assert json.loads(out.response)["findings"][0]["id"] == "S1"


def test_terminal_submission_succeeds_without_assistant_text():
    class Backend:
        def run(self, request):
            assert request.submission is not None
            request.submission.submit({"intent_profile": {"a": 1}})
            return RunResult(
                final_content=request.submission.canonical_output(),
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                raw_stdout="",
                submission=request.submission.snapshot(),
            )

    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", backend=Backend())
    assert out.valid is True
    assert out.response == '{"intent_profile":{"a":1}}'
    assert out.submission_status == "accepted"
    assert out.attempts_detail[0].outcome == "submission_valid"


def test_accepted_submission_ignores_backend_final_content():
    class Backend:
        def run(self, request):
            assert request.submission is not None
            request.submission.submit({"intent_profile": {"a": 1}})
            return RunResult(
                final_content='{"unvalidated":"backend value"}',
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", backend=Backend())

    assert out.valid is True
    assert out.response == '{"intent_profile":{"a":1}}'


def test_schema_backed_raw_json_without_submission_call_is_rejected():
    run_fn = _runner([FakeResult(_VALID_SECURITY, submit_output=False)])
    out = run_agent_with_ovg(
        agent="Security",
        context="ctx",
        run_fn=run_fn,
        max_attempts=1,
    )

    assert out.valid is False
    assert out.response == ""
    assert out.submission_status == "missing"
    assert out.attempts_detail[0].outcome == "submission_missing"


@pytest.mark.parametrize(
    ("value", "schema_type"),
    [
        ([], "array"),
        ({}, "object"),
        (0, "integer"),
        ("", "string"),
    ],
)
def test_schema_accepted_empty_root_is_authoritative(tmp_path, value, schema_type):
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "empty.schema.yaml").write_text(
        yaml.safe_dump({"type": schema_type}),
        encoding="utf-8",
    )
    base = _CONFIG
    entry = replace(
        base.by_key["Profiler_Intent"],
        output_schema="empty.schema.yaml",
        ovg_gates=({"gate": "json_schema"},),
    )
    configuration = replace(
        base,
        root=tmp_path,
        entries=(entry,),
        by_key={entry.key: entry},
        terminal_agents=frozenset(),
        non_graph_infra_agents=frozenset(),
    )

    class Backend:
        def run(self, request):
            assert request.submission is not None
            assert request.submission.submit(value).status == "accepted"
            return RunResult(
                final_content=request.submission.canonical_output(),
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        backend=Backend(),
        configuration=configuration,
        max_attempts=1,
    )

    assert out.valid is True
    assert json.loads(out.response) == value
    assert out.submission_status == "accepted"
    assert out.attempts_detail[0].outcome == "submission_valid"
    assert out.attempts_detail[0].errors == []


def test_rejected_then_corrected_submission_stays_within_one_outer_attempt():
    class Backend:
        def run(self, request):
            assert request.submission is not None
            rejected = request.submission.submit({"wrong": True})
            assert rejected.status == "rejected"
            accepted = request.submission.submit({"intent_profile": {"a": 1}})
            assert accepted.status == "accepted"
            return RunResult(
                final_content=request.submission.canonical_output(),
                tool_call_count=0,
                rounds=2,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", backend=Backend())
    assert out.valid is True and out.attempts == 1
    assert [call["status"] for call in out.attempts_detail[0].submissions] == [
        "rejected",
        "accepted",
    ]
    assert out.attempts_detail[0].submissions[0]["correction"] == 1


def test_repeated_submission_defect_is_fingerprinted_without_feedback_duplication():
    class Backend:
        def run(self, request):
            assert request.submission is not None
            request.submission.submit({"wrong": True})
            return RunResult(
                final_content="",
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        backend=Backend(),
        max_attempts=2,
    )
    first, second = (detail.submissions[0] for detail in out.attempts_detail)
    assert first["diagnosticFingerprint"] == second["diagnosticFingerprint"]
    assert second["repeated"] is True
    assert "feedback" not in first
    assert "feedback" not in second


def test_missing_submission_keeps_raw_response_only_on_backend_result():
    raw_candidate = '{"intent_profile":{"secret":"candidate payload"}}'

    class Backend:
        def run(self, request):
            assert request.submission is not None
            self.result = RunResult(
                final_content=raw_candidate,
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )
            return self.result

    backend = Backend()
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        backend=backend,
        max_attempts=1,
    )
    assert out.valid is False and out.response == ""
    assert out.submission_status == "missing"
    assert out.attempts_detail[0].outcome == "submission_missing"
    assert backend.result.raw_stdout == raw_candidate
    assert backend.result.final_content == ""


def test_schema_retry_persists_redacted_feedback_but_sends_exact_feedback(tmp_path):
    secret = "submitted-secret-enum-value"

    class Backend:
        def __init__(self):
            self.prompts = []

        def run(self, request):
            assert request.submission is not None
            self.prompts.append(request.prompt)
            value = json.loads(_VALID_SECURITY)
            if len(self.prompts) == 1:
                value["findings"][0]["severity"] = secret
            request.submission.submit(value)
            return RunResult(
                final_content=json.dumps(value),
                tool_call_count=0,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    backend = Backend()
    out = run_agent_with_ovg(
        agent="Security",
        context="ctx",
        backend=backend,
        max_attempts=2,
    )

    assert out.valid is True
    assert secret in backend.prompts[1]
    assert secret not in out.attempts_detail[1].retry_feedback

    from roundtable.persistence.trace import build_trace, persist_session
    from roundtable.persistence.usage_summary import build_usage_summary
    from roundtable.reporting import build_report

    persisted = json.dumps(
        {
            "trace": build_trace(session_id="s", agent_outcomes={"Security": out}),
            "usage": build_usage_summary(
                session_id="s",
                outcome_label=None,
                agent_outcomes={"Security": out},
            ),
        },
        sort_keys=True,
    )
    assert secret not in persisted
    artifacts = persist_session(
        tmp_path,
        session_id="s",
        agent_outcomes={"Security": out},
    )
    assert secret not in build_report(artifacts.session_dir)


def test_first_attempt_hint_requires_terminal_submission():
    from roundtable.engine.agent_runner import build_output_contract
    from roundtable.graph.model import get_entry

    contract = build_output_contract(get_entry("Security", _CONFIG), _SCHEMA_DIR)
    assert contract.startswith("## Output contract")
    assert "Call `roundtable_submit_output`" in contract
    assert "call the tool again in the same turn" in contract
    assert "Do not print, repeat, or wrap" in contract
    # The directive is generic — it must NOT hard-code last-run preamble strings.
    assert "Now I have" not in contract

    # And the split holds: the first-attempt payload does NOT restate the contract.
    run_fn = _runner([FakeResult(_VALID_SECURITY)])
    run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert "roundtable_submit_output" not in run_fn.calls[0]["prompt"]


def test_retry_then_success_includes_constraint():
    # Profiler_Intent requires an `intent_profile` object.
    run_fn = _runner(
        [
            FakeResult("not json at all"),  # attempt 1: format fail
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),  # attempt 2: valid
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert out.attempts == 2
    # The retry-causing reject is captured structurally on attempt 1, not as a flat
    # agent-level warning string.
    assert out.attempts_detail[0].outcome == "submission_missing"
    assert out.attempts_detail[0].errors[0]["gate"] == "format"
    # With session reuse (the default), an OVG-reject retry resumes the same
    # session with a feedback-only payload — the corrective constraint, but NOT a
    # re-sent full context or a "PREVIOUS ATTEMPT OUTPUT:" echo (the prior turn is
    # already live in the resumed session).
    first_prompt = run_fn.calls[0]["prompt"]
    second_prompt = run_fn.calls[1]["prompt"]
    assert "Invalid JSON:" in second_prompt
    assert "PREVIOUS ATTEMPT OUTPUT:" not in second_prompt
    assert "ctx" not in second_prompt  # full context not re-sent on a resumed turn
    # Both turns target the SAME (reused) session id.
    assert run_fn.calls[0]["session_id"]
    assert run_fn.calls[1]["session_id"] == run_fn.calls[0]["session_id"]
    assert "ctx" in first_prompt  # attempt 1 carried the full context


def test_gate_failure_retry_injects_the_gate_hint():
    # A GATE failure (valid JSON, missing the required `findings` array) fails the
    # `json_schema` gate, whose crafted hint file (configs/inspectorx/hints/json_schema.md) must be
    # surfaced to the model on the retry so it gets meaningful corrective feedback —
    # not just the raw error list.
    run_fn = _runner(
        [
            FakeResult(json.dumps({"foo": 1})),  # attempt 1: json_schema gate fail
            FakeResult(json.dumps({"findings": []})),  # attempt 2: valid (clean result)
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is True and out.attempts == 2
    second_prompt = run_fn.calls[1]["prompt"]
    assert "OUTPUT REJECTED" in second_prompt
    # The gate's crafted hint (why/how) is injected verbatim from json_schema.md.
    assert "CORRECTIVE ACTION" in second_prompt
    assert "Re-read the field-level errors below" in second_prompt


def test_retry_feedback_is_captured_per_attempt_and_matches_what_was_sent():
    # The steering that was prepended to each retry attempt is persisted on the
    # per-attempt observability record — empty on attempt 1 (no prior failure),
    # and byte-identical to the feedback actually sent on the resumed retry.
    run_fn = _runner(
        [
            FakeResult("not json at all"),  # attempt 1: format fail
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),  # attempt 2: valid
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is True and out.attempts == 2
    detail = out.attempts_detail
    assert detail[0].retry_feedback == ""  # attempt 1 had no prior failure to steer it
    assert "Invalid JSON:" in detail[1].retry_feedback
    # The resumed retry payload is exactly the captured feedback plus the static
    # schema anchor — so the persisted feedback is a verbatim prefix of what was sent.
    assert run_fn.calls[1]["prompt"].startswith(detail[1].retry_feedback)


def test_missing_submission_explains_the_failure_and_recovery_action():
    run_fn = _runner(
        [
            FakeResult("", tool_call_count=3),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)

    first, second = out.attempts_detail
    assert first.outcome == "submission_missing"
    assert first.errors[0]["message"] == (
        "The turn ended after 3 tool calls without calling roundtable_submit_output, "
        "so no structured output was submitted for validation."
    )
    assert second.retry_feedback.startswith("OUTPUT NOT SUBMITTED")
    assert "call roundtable_submit_output" in second.retry_feedback
    assert run_fn.calls[1]["prompt"].startswith(second.retry_feedback)


def test_no_session_reuse_uses_legacy_full_context_retry():
    # --no-session-reuse makes every attempt re-send the
    # full context plus the echoed previous output, and no --session-id is passed.
    run_fn = _runner(
        [
            FakeResult("not json at all"),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent", context="ctx", run_fn=run_fn, session_reuse=False
    )
    assert out.valid is True and out.attempts == 2
    second_prompt = run_fn.calls[1]["prompt"]
    assert "PREVIOUS ATTEMPT OUTPUT:" in second_prompt
    assert "Invalid JSON:" in second_prompt
    assert "ctx" in second_prompt  # full context IS re-sent
    assert run_fn.calls[0]["session_id"] is None
    assert run_fn.calls[1]["session_id"] is None


def test_sdk_failure_stops_without_retry():
    run_fn = _runner(
        [
            FakeResult(
                "",
                exit_code=1,
                raw_stderr='Model "model-a" is not available.',
                backend_outcome=BackendOutcome.MODEL_UNAVAILABLE,
            ),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        run_fn=run_fn,
        model="model-a",
    )
    assert out.valid is False
    assert out.attempts == 1
    assert len(run_fn.calls) == 1
    assert out.model == "model-a"
    assert out.attempts_detail[0].outcome == "api_error"
    assert out.attempts_detail[0].errors[0]["kind"] == "api_error"


def test_accepted_submission_survives_a_failing_backend_process():
    # The transport validated and holds the payload; how the process ended afterwards
    # cannot unmake that, so the turn must not be written off as a run failure.
    run_fn = _runner(
        [
            FakeResult(
                _VALID_SECURITY,
                exit_code=1,
                raw_stderr="session ended unexpectedly",
                backend_outcome=BackendOutcome.SESSION_ERROR,
            )
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert json.loads(out.response)["findings"][0]["id"] == "S1"
    assert out.attempts_detail[0].outcome == "submission_valid"


def test_accepted_submission_still_reports_the_backend_fault():
    # Accepting the output must not bury the fault: the run stays investigable.
    run_fn = _runner(
        [
            FakeResult(
                _VALID_SECURITY,
                exit_code=1,
                raw_stderr="session ended unexpectedly",
                backend_outcome=BackendOutcome.SESSION_ERROR,
            )
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    notices = [w for w in out.warnings if w.get("kind") == "backend_unclean_exit"]
    assert len(notices) == 1
    assert "session_error: session ended unexpectedly" in notices[0]["message"]
    assert out.attempts_detail[0].backend_outcome == "session_error"


def test_clean_accepted_submission_reports_no_backend_fault():
    run_fn = _runner([FakeResult(_VALID_SECURITY, backend_outcome=BackendOutcome.SUCCESS)])
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert [w for w in out.warnings if w.get("kind") == "backend_unclean_exit"] == []
    assert out.attempts_detail[0].backend_outcome is None


def test_rejected_submission_with_a_failing_process_is_still_a_run_failure():
    # Only acceptance overrides a bad exit — a rejection must not slip through.
    run_fn = _runner(
        [
            FakeResult(
                json.dumps({"wrong": "shape"}),
                exit_code=1,
                raw_stderr="transport closed",
                backend_outcome=BackendOutcome.TRANSPORT,
            )
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.attempts_detail[0].outcome == "api_error"


def test_run_failure_names_the_classified_cause_not_the_exit_code():
    # An exit code collapses every fault onto 1; the classification is what an
    # investigation can act on, so it has to reach the reported reason.
    run_fn = _runner(
        [
            FakeResult(
                "",
                exit_code=1,
                raw_stderr="429 rate limited, retry after 60s",
                backend_outcome=BackendOutcome.RATE_LIMITED,
            )
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.last_error == "rate_limited: 429 rate limited, retry after 60s"
    assert out.attempts_detail[0].backend_outcome == "rate_limited"
    assert out.attempts_detail[0].errors[0]["message"] == out.last_error


def test_unclassified_run_failure_reports_an_unknown_cause():
    run_fn = _runner([FakeResult("", exit_code=1)])
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.last_error == "unknown"
    assert out.attempts_detail[0].backend_outcome is None


def test_timeout_keeps_its_own_reported_reason():
    # result_access matches this exact wording to say "timed out" rather than
    # "the model run failed" — a classification label must not displace it.
    run_fn = _runner(
        [
            FakeResult(
                "",
                exit_code=None,
                timed_out=True,
                timeout_phase="tool_running",
                raw_stderr="deadline exceeded",
                backend_outcome=BackendOutcome.TIMEOUT,
            )
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.last_error == TIMEOUT_ERROR
    assert out.attempts_detail[0].errors[0]["message"] == f"{TIMEOUT_ERROR} (phase=tool_running)"


def test_model_wait_timeout_retries_once_fresh_without_spending_ovg_budget():
    run_fn = _runner(
        [
            FakeResult(
                "",
                exit_code=None,
                timed_out=True,
                timeout_phase="model_wait",
                timeout_snapshot=[],
            ),
            FakeResult("not json"),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn, max_attempts=2)
    assert out.valid is True
    assert out.attempts == 3
    assert [d.outcome for d in out.attempts_detail] == [
        "api_error",
        "submission_missing",
        "submission_valid",
    ]
    assert out.attempts_detail[0].timeout_phase == "model_wait"
    assert run_fn.calls[0]["session_id"] != run_fn.calls[1]["session_id"]
    assert run_fn.calls[2]["session_id"] == run_fn.calls[1]["session_id"]


@pytest.mark.parametrize("phase", ["tool_running", "background_tool_wait", "cleanup", None])
def test_non_model_or_ambiguous_timeout_never_retries(phase):
    run_fn = _runner(
        [
            FakeResult(
                "",
                exit_code=None,
                timed_out=True,
                timeout_phase=phase,
                timeout_snapshot=[{"name": "powershell", "shellId": "s", "activeAtTimeout": True}],
            ),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.attempts == 1
    assert len(run_fn.calls) == 1
    assert out.last_error == "timeout"
    assert out.attempts_detail[0].timeout_snapshot[0]["shellId"] == "s"
    if phase:
        assert f"phase={phase}" in out.attempts_detail[0].errors[0]["message"]


def test_model_wait_timeout_is_retried_at_most_once():
    run_fn = _runner(
        [
            FakeResult("", exit_code=None, timed_out=True, timeout_phase="model_wait"),
            FakeResult("", exit_code=None, timed_out=True, timeout_phase="model_wait"),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.attempts == 2
    assert len(run_fn.calls) == 2


def test_schema_backed_prose_is_never_accepted():
    run_fn = _runner(
        [
            FakeResult("a much longer piece of prose analysis"),
            FakeResult("mid prose"),
            FakeResult("final attempt prose"),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.attempts == DEFAULT_MAX_ATTEMPTS
    assert out.valid is False
    assert out.response == ""
    assert [d.outcome for d in out.attempts_detail] == ["submission_missing"] * 3


def test_schema_backed_output_does_not_resurrect_earlier_text():
    run_fn = _runner(
        [
            FakeResult(
                "a much longer piece of prose analysis that should win"
            ),  # format fail, retry
            FakeResult("mid"),  # format fail, retry
            FakeResult("", exit_code=1),  # API fail (no text)
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.response == ""


def test_unknown_schema_less_agent_returns_first_backend_output_as_is():
    run_fn = _runner([FakeResult("  schema-less prose\n"), FakeResult("unused")])
    out = run_agent_with_ovg(agent="UnknownAgentXYZ", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert out.attempts == 1
    assert len(run_fn.calls) == 1
    assert out.response == "  schema-less prose\n"
    assert out.submission_status is None
    assert out.gate is None
    assert out.attempts_detail[0].outcome == "raw_output"


def test_all_api_failures_abort():
    run_fn = _runner(
        [
            FakeResult("", exit_code=1),
            FakeResult("", exit_code=1),
            FakeResult("", exit_code=1),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.response == ""
    assert any(w["kind"] == "abort" for w in out.warnings)


def test_unknown_schema_less_agent_does_not_interpret_json_shaped_text():
    run_fn = _runner([FakeResult("{}")])
    out = run_agent_with_ovg(agent="UnknownAgentXYZ", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert out.response == "{}"
    assert out.attempts_detail[0].outcome == "raw_output"


# Substantive JSON that parses fine but fails a non-format gate. Under the
# gate-aware fallback these must NOT be resurrected as raw — only ``format``-gate
# (non-JSON prose) output is preservable.
_SCHEMA_FAIL_SECURITY = json.dumps({"wrong": "shape"})  # missing required 'findings'
_SEMANTIC_FAIL_SECURITY = json.dumps(
    {
        "findings": [
            {
                "id": "S1",
                "title": "x",
                "severity": "low",
                "description": "d",
                "locations": [],  # passes schema presence, fails semantic (>=1 entry)
                "impact": "i",
                "fix": "Escape the interpolated identifier before building the SQL string.",
            }
        ]
    }
)


def test_schema_failure_is_not_forwarded():
    run_fn = _runner([FakeResult(_SCHEMA_FAIL_SECURITY)] * DEFAULT_MAX_ATTEMPTS)
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.response == ""
    assert out.gate == "submission"
    assert out.attempts_detail[-1].errors[0]["gate"] == "json_schema"
    assert any(w["kind"] == "abort" for w in out.warnings)


def test_max_attempts_caps_the_retry_loop():
    # A custom budget (2) must stop the loop after exactly 2 invalid attempts,
    # even though more scripted results remain — proving max_attempts is honoured,
    # not merely accepted as a parameter.
    run_fn = _runner([FakeResult(_SCHEMA_FAIL_SECURITY)] * 5)
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn, max_attempts=2)
    assert out.attempts == 2
    assert len(run_fn.calls) == 2
    assert out.valid is False


def test_p0_semantic_failure_not_resurrected_as_raw():
    # 3x JSON that clears format+schema but fails the semantic gate (empty
    # locations[]). A semantically rejected finding must never leak downstream.
    run_fn = _runner([FakeResult(_SEMANTIC_FAIL_SECURITY)] * DEFAULT_MAX_ATTEMPTS)
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.valid is False
    assert out.response == ""
    assert out.gate == "submission"
    assert out.attempts_detail[-1].errors[0]["gate"] == "locations_floor"
    assert any(w["kind"] == "abort" for w in out.warnings)


# ── changed_files grounding-context threading ────────────────────────────────


def test_p1_changed_files_forwarded_to_validation(monkeypatch):
    """changed_files threads into evaluate_agent_output's grounding context."""
    import roundtable.engine.agent_runner.run_loop as rl

    captured: dict = {}
    real = rl.evaluate_agent_value

    def spy(agent, output, context, **kwargs):
        captured["context"] = context
        return real(agent, output, context, **kwargs)

    monkeypatch.setattr(rl, "evaluate_agent_value", spy)
    run_fn = _runner([FakeResult(_VALID_SECURITY)])
    out = run_agent_with_ovg(
        agent="Security",
        context="ctx",
        run_fn=run_fn,
        changed_files=["a.cs", "b.cs"],
    )
    assert out.valid is True
    assert captured["context"] == {"changed_files": ["a.cs", "b.cs"], "tool_calls": []}


def test_p1_changed_files_defaults_to_empty(monkeypatch):
    """Omitted changed_files means an empty list and skips grounding."""
    import roundtable.engine.agent_runner.run_loop as rl

    captured: dict = {}
    real = rl.evaluate_agent_value

    def spy(agent, output, context, **kwargs):
        captured["context"] = context
        return real(agent, output, context, **kwargs)

    monkeypatch.setattr(rl, "evaluate_agent_value", spy)
    run_fn = _runner([FakeResult(_VALID_SECURITY)])
    run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert captured["context"] == {"changed_files": [], "tool_calls": []}


# ── usage threading ──────────────────────────────────────────────────────────


from roundtable.backend.usage import (  # noqa: E402
    EMPTY_USAGE,
    AgentUsage,
    BillingStatus,
    BillingValue,
    merge_usage,
)


@dataclass
class FakeUsageResult:
    """Fake result that carries stdout-derived token_usage."""

    final_content: str
    token_usage: AgentUsage = EMPTY_USAGE
    exit_code: int | None = 0
    timed_out: bool = False
    tool_call_count: int = 0
    raw_stderr: str = ""
    backend_outcome: BackendOutcome | None = None
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and bool(self.final_content)


def _usage_runner(scripted):
    """run_fn that returns scripted FakeUsageResults, recording session_id per call."""
    calls = []
    it = iter(scripted)

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **_kw):
        calls.append({"agent": agent, "session_id": _kw.get("session_id")})
        result = next(it)
        submission = _kw.get("submission")
        if submission is not None:
            try:
                output = json.loads(result.final_content)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                submission.submit(output, tool_calls=result.tool_calls)
        return result

    run_fn.calls = calls  # type: ignore[attr-defined]
    return run_fn


def test_no_usage_from_run_fn_means_empty_usage():
    # An injected fake that returns no token_usage ⇒ aggregate usage is EMPTY.
    run_fn = _runner([FakeResult(_VALID_SECURITY)])
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert out.usage.is_empty


def test_usage_accumulates_across_attempts():
    # Two attempts (format fail → valid); usage from BOTH attempts is summed.
    run_fn = _usage_runner(
        [
            FakeUsageResult(
                "not json",
                token_usage=AgentUsage(output_tokens=10, rounds=1),
            ),
            FakeUsageResult(
                json.dumps({"intent_profile": {"a": 1}}),
                token_usage=AgentUsage(output_tokens=20, rounds=1),
            ),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        run_fn=run_fn,
    )
    assert out.valid is True
    assert out.usage.output_tokens == 30  # 10 + 20 across attempts
    assert out.usage.rounds == 2


def test_resumed_retry_counts_sdk_billing_as_delta_not_cumulative():
    run_fn = _usage_runner(
        [
            FakeUsageResult(
                "not json",  # attempt 1: format-reject → resume
                token_usage=AgentUsage(
                    billing=BillingValue.complete(1_000_000_000.0, 1.0),
                    duration_ms=100.0,
                    rounds=1,
                ),
            ),
            FakeUsageResult(
                json.dumps({"intent_profile": {"a": 1}}),  # attempt 2 (resumed): cumulative
                token_usage=AgentUsage(
                    billing=BillingValue.complete(2_500_000_000.0, 2.5),
                    duration_ms=250.0,
                    rounds=1,
                ),
            ),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is True and out.attempts == 2
    assert out.usage.billing == BillingValue.complete(2_500_000_000.0, 2.5)
    assert out.attempts_detail[1].usage.billing == BillingValue.complete(
        1_500_000_000.0,
        1.5,
    )
    assert out.usage.duration_ms == 250.0  # 100.0 + delta(250.0-100.0), not 350.0


def test_no_session_reuse_sums_fresh_sdk_billing_snapshots():
    run_fn = _usage_runner(
        [
            FakeUsageResult(
                "not json",
                token_usage=AgentUsage(
                    billing=BillingValue.complete(1_000_000_000.0, 1.0),
                    duration_ms=100.0,
                    rounds=1,
                ),
            ),
            FakeUsageResult(
                json.dumps({"intent_profile": {"a": 1}}),
                token_usage=AgentUsage(
                    billing=BillingValue.complete(2_000_000_000.0, 2.0),
                    duration_ms=120.0,
                    rounds=1,
                ),
            ),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        run_fn=run_fn,
        session_reuse=False,
    )
    assert out.valid is True and out.attempts == 2
    assert out.usage.billing == BillingValue.complete(3_000_000_000.0, 3.0)
    assert out.usage.duration_ms == 220.0


def test_unavailable_attempt_billing_suppresses_partial_agent_total():
    run_fn = _usage_runner(
        [
            FakeUsageResult(
                "not json",
                token_usage=AgentUsage(
                    billing=BillingValue.complete(1_000_000_000.0, 1.0),
                    rounds=1,
                ),
            ),
            FakeUsageResult(
                json.dumps({"intent_profile": {"a": 1}}),
                token_usage=AgentUsage(
                    billing=BillingValue.unavailable(),
                    rounds=1,
                ),
            ),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is True
    assert out.usage.billing.status is BillingStatus.UNAVAILABLE
    assert out.attempts_detail[1].warnings[0]["kind"] == "sdk_usage_metrics_unavailable"


# ── per-attempt OVG observability ────────────────────────────────────────────


def test_attempts_detail_single_success():
    run_fn = _runner(
        [
            FakeResult(
                _VALID_SECURITY, tool_call_count=2, tools_used=["view", "grep"], wall_clock_s=1.5
            )
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    assert len(out.attempts_detail) == 1
    d = out.attempts_detail[0]
    assert d.attempt == 1
    assert d.outcome == "submission_valid"
    assert d.errors == []
    assert d.tools_invoked == ["view", "grep"]
    assert d.wall_clock_ms == 1500.0
    assert out.wall_clock_ms_total == 1500.0


def test_attempts_detail_records_redacted_tool_calls_per_attempt():
    # Each attempt carries its own redacted {name, args?} calls: the allowlist is
    # applied uniformly to builtin AND MCP tools (identifying refs kept, payloads
    # and unknown keys dropped).
    run_fn = _runner(
        [
            FakeResult(
                _VALID_SECURITY,
                tool_call_count=2,
                tools_used=["view", "ado-get_pull_request"],
                tool_calls=[
                    {"name": "view", "args": {"path": "src/a.py", "secret": "s"}},
                    {"name": "ado-get_pull_request", "args": {"pullRequestId": 7, "body": "x"}},
                ],
            )
        ]
    )
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    d = out.attempts_detail[0]
    assert d.tool_calls == [
        {
            "name": "view",
            "args": {"path": "src/a.py"},
            "argumentRetention": {"dropped": {"secret": "unretained"}},
        },
        {
            "name": "ado-get_pull_request",
            "args": {"pullRequestId": 7},
            "argumentRetention": {"dropped": {"body": "sensitive"}},
        },
    ]


def test_attempts_detail_retry_then_success_records_reason_not_text():
    run_fn = _runner(
        [
            FakeResult("not json at all"),  # attempt 1: format reject
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),  # attempt 2: valid
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert [d.outcome for d in out.attempts_detail] == [
        "submission_missing",
        "submission_valid",
    ]
    err = out.attempts_detail[0].errors[0]
    assert err["gate"] == "format"
    # Persisted parser diagnostics keep the location but not the raw candidate excerpt.
    assert "character 0" in err["message"]
    assert "^" not in err["message"]
    assert "snippet" not in err
    assert out.attempts_detail[1].errors == []


def test_attempts_detail_format_reject_omits_raw_candidate_snippet():
    run_fn = _runner(
        [
            FakeResult("Looking at the code, here is my analysis..."),  # format reject
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),  # valid
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    d0 = out.attempts_detail[0]
    assert [e["gate"] for e in d0.errors] == ["format"]
    assert "snippet" not in d0.errors[0]
    # The clean attempt records no errors.
    assert out.attempts_detail[1].errors == []


def test_attempts_detail_schema_reject_redacts_value_and_keeps_schema_details():
    run_fn = _runner([FakeResult(_SCHEMA_FAIL_SECURITY)] * DEFAULT_MAX_ATTEMPTS)
    out = run_agent_with_ovg(agent="Security", context="ctx", run_fn=run_fn)
    d0 = out.attempts_detail[0]
    assert "json_schema" in {e["gate"] for e in d0.errors}
    assert all("gate" in e and "message" in e for e in d0.errors)
    assert all("actual" not in e and "snippet" not in e for e in d0.errors)
    assert any(e.get("keyword") == "required" and e.get("expected") for e in d0.errors)
    assert "wrong" not in json.dumps(d0.errors)


def test_attempts_detail_per_attempt_usage_sums_to_aggregate():
    run_fn = _usage_runner(
        [
            FakeUsageResult(
                "not json",
                token_usage=AgentUsage(output_tokens=10, rounds=1),
            ),
            FakeUsageResult(
                json.dumps({"intent_profile": {"a": 1}}),
                token_usage=AgentUsage(output_tokens=20, rounds=1),
            ),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        run_fn=run_fn,
    )
    # Invariant: per-attempt usages sum exactly to the aggregate.
    summed = merge_usage(d.usage for d in out.attempts_detail)
    assert summed.output_tokens == out.usage.output_tokens == 30
    assert summed.rounds == out.usage.rounds == 2


def test_attempts_detail_records_api_error():
    run_fn = _runner(
        [
            FakeResult("", exit_code=1),
            FakeResult(json.dumps({"intent_profile": {"a": 1}})),
        ]
    )
    out = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        run_fn=run_fn,
        model="model-a",
    )
    assert out.attempts_detail[0].outcome == "api_error"
    assert out.attempts_detail[0].model == "model-a"
    assert out.attempts_detail[0].errors[0]["kind"] == "api_error"
    assert len(out.attempts_detail) == 1


def test_attempts_detail_records_missing_submission_on_last_attempt():
    run_fn = _runner(
        [
            FakeResult("prose one"),
            FakeResult("prose two"),
            FakeResult("final attempt prose"),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.attempts == DEFAULT_MAX_ATTEMPTS
    assert [d.outcome for d in out.attempts_detail] == [
        "submission_missing",
        "submission_missing",
        "submission_missing",
    ]


def test_schema_hint_teaches_canonical_locations():
    """The first-attempt anchor is rendered from the agent's schema ``examples[0]``,
    so every finding-bearing agent's anchor teaches the canonical
    ``locations:[{filePath,startLine,endLine}]`` shape (never the retired singular
    ``file``/``line`` shape), and each schema's own example passes the runtime gate
    pipeline it advertises. Guards against the anchor drifting back to an opaque
    placeholder or the legacy location shape.
    """
    from roundtable.engine.agent_runner import build_schema_hint
    from roundtable.graph.model import get_entry
    from roundtable.validation.gates import load_schema_document
    from roundtable.validation.pipeline import evaluate_agent_output

    finding_agents = [
        "Security",
        "Optimizer",
        "SchemaDrift",
        "Specialist_A11y",
        "Architecture",
        "Dependency",
        "CodeCorrectness",
        "Deadlock",
        "DocsKeeper",
        "TestQuality",
        "AttackSurfaceScanner",
        "Privacy",
        "Analyst_Patterns",
        "Analyst_Logic",
        "Analyst_Standards",
    ]
    for key in finding_agents:
        entry = get_entry(key, _CONFIG)
        assert entry is not None and entry.output_schema, key
        schema = load_schema_document(entry.output_schema, _SCHEMA_DIR)
        example = (schema.get("examples") or [None])[0]
        assert isinstance(example, dict), key

        # A non-empty finding item must use the canonical location shape, and the
        # anchor rendered from it must surface that shape verbatim.
        findings = example.get("findings") or []
        if findings:
            item = findings[0]
            assert item.get("locations"), key
            loc0 = item["locations"][0]
            assert set(loc0) == {"filePath", "startLine", "endLine"}, key
            assert "file" not in item and "line" not in item, key

            hint = build_schema_hint(key, _CONFIG)
            for token in ('"locations"', '"filePath"', '"startLine"', '"endLine"'):
                assert token in hint, (key, token)

        # The schema's own canonical example passes the runtime gate pipeline.
        res = evaluate_agent_output(
            key,
            json.dumps(example),
            {"changed_files": []},
            configuration=_CONFIG,
        )
        assert res.passed, (key, res.gate, res.error_messages())


def test_resolve_output_example_prefers_declared_document():
    """When an agent declares ``output_example``, it wins over the schema's own
    ``examples[0]`` — the mechanism that lets finding-agents sharing the minimal
    ``finding_min`` schema advertise their own richer canonical shape."""
    import types

    from roundtable.engine.agent_runner import _resolve_output_example

    schema = {"examples": [{"marker": "from-schema"}]}
    entry = types.SimpleNamespace(output_example="examples/codecorrectness.example.yaml")
    resolved = _resolve_output_example(entry, schema, _SCHEMA_DIR)
    # The real example file's root is a findings-bearing object, never the schema stub.
    assert isinstance(resolved, dict) and resolved.get("marker") != "from-schema"
    assert "findings" in resolved


def test_resolve_output_example_falls_back_to_schema_example():
    """No declared ``output_example`` → fall back to the schema's ``examples[0]``."""
    import types

    from roundtable.engine.agent_runner import _resolve_output_example

    schema = {"examples": [{"marker": "from-schema"}]}
    entry = types.SimpleNamespace(output_example=None)
    assert _resolve_output_example(entry, schema) == {"marker": "from-schema"}


def test_resolve_output_example_is_fail_soft_on_bad_path():
    """An unloadable ``output_example`` never breaks the hint — it falls back to the
    schema example (doctor coherence is what surfaces the misconfiguration)."""
    import types

    from roundtable.engine.agent_runner import _resolve_output_example

    schema = {"examples": [{"marker": "from-schema"}]}
    entry = types.SimpleNamespace(output_example="examples/does-not-exist.yaml")
    assert _resolve_output_example(entry, schema) == {"marker": "from-schema"}


def test_build_schema_hint_renders_declared_output_example():
    """End-to-end: a finding-agent with a declared ``output_example`` gets a hint whose
    canonical example is rendered from that document (its findings, not the shared
    schema's placeholder)."""
    from roundtable.engine.agent_runner import build_schema_hint
    from roundtable.graph.model import get_entry
    from roundtable.validation.gates import load_schema_document

    entry = get_entry("CodeCorrectness", _CONFIG)
    assert entry is not None and entry.output_example
    example_doc = load_schema_document(entry.output_example, _SCHEMA_DIR)
    hint = build_schema_hint("CodeCorrectness", _CONFIG)
    # A distinctive value from the declared example surfaces verbatim in the hint.
    first_finding = (example_doc.get("findings") or [None])[0]
    assert isinstance(first_finding, dict)
    assert json.dumps(first_finding["type"], ensure_ascii=False) in hint


def test_build_schema_hint_surfaces_canonical_field_meanings():
    """The hint teaches each canonical vocabulary term the agent's schema references,
    surfacing its one-line ``$def`` description (including enum semantics) with no
    per-prompt copy, and expands transitively (``original_severity`` pulls in
    ``severity``)."""
    from roundtable.engine.agent_runner import build_schema_hint

    hint = build_schema_hint("Analyst_Patterns", _CONFIG)
    assert "Field meanings (canonical vocabulary" in hint
    # A referenced term surfaces with its enum semantics from the vocabulary $def.
    assert "- severity:" in hint
    assert "critical = data loss" in hint
    assert "- category:" in hint

    # Transitive: SeverityInflator references original_severity, which $refs severity,
    # so BOTH appear even though the item schema names only original_severity's parent.
    inflator = build_schema_hint("SeverityInflator", _CONFIG)
    assert "- finding_id:" in inflator
    assert "- original_severity:" in inflator
    assert "- severity:" in inflator


def test_session_id_mismatch_adopts_reported_id_for_resume():
    """If the CLI reports a different session id than requested,
    the loop ADOPTS the reported id (so the subsequent resume targets the real session)
    and records a ``session_adopt`` notice."""
    run_fn = _runner(
        [
            FakeResult("not json at all", session_id="server-xyz"),  # missing submission
            FakeResult(json.dumps({"intent_profile": {"a": 1}}), session_id="server-xyz"),
        ]
    )
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert out.valid is True and out.attempts == 2
    # Attempt 1 requested a minted uuid; the resumed attempt 2 targets the ADOPTED id.
    assert run_fn.calls[0]["session_id"] != "server-xyz"
    assert run_fn.calls[1]["session_id"] == "server-xyz"
    assert any(w["kind"] == "session_adopt" and "server-xyz" in w["message"] for w in out.warnings)


def test_mcp_servers_telemetry_is_collected_and_deduped_by_name():
    """The loop unions ``result.mcp_servers`` across attempts into ``out.mcp_servers``,
    keyed by name so a server reported on multiple attempts appears exactly once
    in telemetry."""
    r1 = FakeResult("not json at all")  # attempt 1: OVG reject
    r1.mcp_servers = [{"name": "ado", "status": "loaded", "transport": "stdio"}]
    r2 = FakeResult(json.dumps({"intent_profile": {"a": 1}}))  # attempt 2: valid
    r2.mcp_servers = [
        {"name": "ado", "status": "loaded", "transport": "stdio"},  # duplicate name
        {"name": "gh", "status": "loaded", "transport": "http"},
    ]
    run_fn = _runner([r1, r2])
    out = run_agent_with_ovg(agent="Profiler_Intent", context="ctx", run_fn=run_fn)
    assert sorted(s["name"] for s in out.mcp_servers) == ["ado", "gh"]  # ado deduped
