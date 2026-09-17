from __future__ import annotations

import pytest

from roundtable import cli
from roundtable.backend import (
    BackendOptions,
    FunctionBackend,
    KeywordBackend,
    OutputSubmission,
    RunRequest,
    RunResult,
    SubmissionValidation,
    backend_context,
    open_backend,
    register_backend,
)
from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration

_CONFIG = get_configuration(resolve_bundle("buddies"))


def test_cli_backend_selection_accepts_registered_names_without_a_selection_branch() -> None:
    assert cli._resolve_backend_name(backend=None, simulate=False) == "copilot"
    assert cli._resolve_backend_name(backend=None, simulate=True) == "mock"
    assert cli._resolve_backend_name(backend="third-party", simulate=False) == "third-party"


def test_cli_backend_and_simulate_conflict_fails_clearly() -> None:
    import pytest

    with pytest.raises(ValueError, match="cannot be combined"):
        cli._resolve_backend_name(backend="mock", simulate=True)


def test_backend_and_simulate_flags_are_mutually_exclusive() -> None:
    import pytest

    parser = cli.build_parser()
    assert parser.parse_args(["review", "--backend", "third-party"]).backend == "third-party"
    with pytest.raises(SystemExit):
        parser.parse_args(["review", "--backend", "mock", "--simulate"])


def test_registry_accepts_a_third_backend_without_cli_changes() -> None:
    expected = RunResult(final_content="{}", tool_call_count=0, rounds=1, exit_code=0)
    register_backend(
        "third-test",
        lambda _options: backend_context(FunctionBackend(lambda _request: expected)),
    )

    with open_backend("third-test", BackendOptions(_CONFIG)) as backend:
        result = backend.run(RunRequest(agent="Any", prompt="input"))

    assert result is expected


def test_registered_mock_backend_consumes_typed_request() -> None:
    configuration = _CONFIG
    with open_backend("mock", BackendOptions(configuration)) as backend:
        result = backend.run(RunRequest(agent="Judge", prompt="input"))

    assert isinstance(result, RunResult)
    assert result.ok is True


def _submission_backend(kind: str, *, accepted: bool):
    def typed(request):
        assert request.submission is not None
        if accepted:
            request.submission.submit({"accepted": True})
        else:
            request.submission.submit({"accepted": False})
        return RunResult(
            final_content='{"backend":"unvalidated"}',
            tool_call_count=0,
            rounds=1,
            exit_code=0,
        )

    if kind == "function":
        return FunctionBackend(typed)

    def keyword(**kwargs):
        request = RunRequest(
            agent=kwargs["agent"],
            prompt=kwargs["prompt"],
            submission=kwargs["submission"],
        )
        return typed(request)

    return KeywordBackend(keyword)


def _submission(*, accepted: bool) -> OutputSubmission:
    return OutputSubmission(
        {"type": "object"},
        lambda value, _calls: SubmissionValidation(
            passed=value == {"accepted": True},
            diagnostics=(
                ()
                if accepted
                else (
                    {
                        "gate": "json_schema",
                        "path": "accepted",
                        "keyword": "const",
                        "message": "must be true",
                        "actual": "false",
                    },
                )
            ),
        ),
    )


@pytest.mark.parametrize("kind", ["function", "keyword"])
def test_backend_adapters_canonicalize_accepted_submission(kind) -> None:
    submission = _submission(accepted=True)
    result = _submission_backend(kind, accepted=True).run(
        RunRequest(agent="Any", prompt="input", submission=submission)
    )

    assert result.final_content == '{"accepted":true}'
    assert result.submission is not None and result.submission.status == "accepted"


@pytest.mark.parametrize("kind", ["function", "keyword"])
def test_backend_adapters_quarantine_unaccepted_schema_content(kind) -> None:
    submission = _submission(accepted=False)
    result = _submission_backend(kind, accepted=False).run(
        RunRequest(agent="Any", prompt="input", submission=submission)
    )

    assert result.final_content == ""
    assert result.raw_stdout == '{"backend":"unvalidated"}'
    assert result.submission is not None and result.submission.status == "rejected"
