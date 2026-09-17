from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from roundtable.backend import OutputSubmission, SubmissionValidation
from roundtable.backend.sdk import build_submission_tool, require_sdk


def _contract() -> OutputSubmission:
    def validate(value, _tool_calls):
        if value == {"answer": 42}:
            return SubmissionValidation(True)
        return SubmissionValidation(
            False,
            (
                {
                    "gate": "json_schema",
                    "path": "answer",
                    "keyword": "const",
                    "message": "value must equal 42",
                    "description": "The final answer.",
                },
            ),
        )

    return OutputSubmission(
        {
            "type": "object",
            "required": ["output"],
            "properties": {"output": {"type": "object"}},
        },
        validate,
    )


def test_sdk_tool_is_terminal_permission_free_non_deferred_and_uses_wrapper_schema():
    contract = _contract()
    tool = build_submission_tool(contract, list)
    assert tool.name == "roundtable_submit_output"
    assert tool.parameters is contract.parameters
    assert tool.skip_permission is True
    assert tool.defer == "never"
    assert tool.is_terminal is True


def test_rejected_call_returns_feedback_then_corrected_call_succeeds_on_same_tool():
    contract = _contract()
    tool = build_submission_tool(contract, lambda: [{"name": "view", "args": {}}])
    bad = asyncio.run(tool.handler(SimpleNamespace(arguments={"output": {"answer": 41}})))
    good = asyncio.run(tool.handler(SimpleNamespace(arguments={"output": {"answer": 42}})))
    assert bad.result_type == "failure"
    assert "answer (const): value must equal 42" in bad.text_result_for_llm
    assert good.result_type == "success"
    assert contract.snapshot().status == "accepted"
    assert contract.canonical_output() == json.dumps({"answer": 42}, separators=(",", ":"))


def test_repeated_identical_defect_says_correction_was_not_applied():
    contract = _contract()
    first = contract.submit({"answer": 41})
    second = contract.submit({"answer": 41})
    assert first.repeated is False
    assert second.repeated is True
    assert "previous correction was not applied" in second.feedback
    assert first.diagnostic_fingerprints == second.diagnostic_fingerprints


def test_retained_parser_diagnostic_redacts_excerpt_and_bounds_actionable_details():
    secret = "parser-secret-value-410d"

    def validate(_value, _tool_calls):
        return SubmissionValidation(
            False,
            (
                {
                    "gate": "format",
                    "path": "x" * 600,
                    "message": (
                        "Invalid JSON: EOF at line 4, column 9, character 88. "
                        f"{secret}\n        ^\n"
                        "Unmatched root object opened at line 1, column 1, character 0."
                    ),
                    "snippet": secret,
                },
            ),
        )

    attempt = OutputSubmission({}, validate).submit(secret)
    retained = attempt.retained_diagnostics()[0]

    assert retained["message"] == (
        "Invalid JSON: EOF at line 4, column 9, character 88. "
        "Unmatched root object opened at line 1, column 1, character 0."
    )
    assert len(retained["path"]) == 500
    assert secret not in json.dumps(retained)


def test_retained_domain_diagnostic_never_keeps_candidate_derived_message():
    secret = "TOPSECRET_TOKEN_IN_PATH"

    def validate(_value, _tool_calls):
        return SubmissionValidation(
            False,
            (
                {
                    "gate": "grounded_locations",
                    "path": "claims[0].locations[0].filePath",
                    "message": f"{secret} is not present in the changed files",
                },
            ),
        )

    attempt = OutputSubmission({}, validate).submit({"filePath": secret})
    retained = attempt.retained_diagnostics()[0]

    assert retained == {
        "gate": "grounded_locations",
        "path": "claims[0].locations[0].filePath",
        "message": "output failed gate 'grounded_locations'",
    }
    assert secret not in attempt.retained_feedback
    assert secret not in json.dumps(retained)


def test_installed_sdk_exposes_parameters_terminal_flag_and_wire_mapping():
    copilot = require_sdk()
    annotations = copilot.Tool.__annotations__
    source = inspect.getsource(copilot.CopilotClient.create_session)
    assert "parameters" in annotations
    assert "is_terminal" in annotations
    assert 'definition["isTerminal"] = True' in source
