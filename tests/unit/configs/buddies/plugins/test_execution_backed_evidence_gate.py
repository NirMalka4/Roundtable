"""execution_backed_evidence gate: bind a ``measured`` claim to a real run."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from roundtable.bundle import resolve_bundle
from roundtable.configs.buddies.plugins.gates import execution_backed_evidence_gate
from roundtable.graph import get_configuration, register_config_plugins
from roundtable.validation.gate_kit import GateRequest
from tests.support.engine import run_agent_with_ovg

_REQ = ["findings[].evidence"]
_BUDDIES_ROOT = "roundtable/configs/buddies"
_SCHEMA = Path(_BUDDIES_ROOT) / "schemas" / "redgreen.schema.yaml"
_CONFIG = get_configuration(resolve_bundle("buddies"))
register_config_plugins(_CONFIG)


@dataclass
class _Result:
    final_content: str
    tool_calls: list[dict] = field(default_factory=list)
    exit_code: int = 0
    timed_out: bool = False
    tool_call_count: int = 0
    raw_stderr: str = ""
    tools_used: list[str] = field(default_factory=list)
    wall_clock_s: float = 0.0

    @property
    def ok(self) -> bool:
        return True


def _runner(results: list[_Result]):
    scripted = iter(results)

    def run_fn(**kwargs):
        result = next(scripted)
        submission = kwargs.get("submission")
        if submission is not None:
            submission.submit(json.loads(result.final_content), tool_calls=result.tool_calls)
        return result

    return run_fn


def _redgreen_output(evidence: str) -> str:
    document = yaml.safe_load(_SCHEMA.read_text(encoding="utf-8"))
    output = copy.deepcopy(document["examples"][0])
    output["findings"][0]["evidence"] = evidence
    return json.dumps(output)


def _run(
    findings: list[dict],
    tool_calls: list | None = None,
    requires: list[str] | None = None,
    **params: object,
) -> list[str]:
    out = {"findings": findings}
    ctx = {"tool_calls": tool_calls} if tool_calls is not None else {}
    diags = execution_backed_evidence_gate(
        GateRequest("redgreen", out, ctx, requires=requires or _REQ, params=params)
    )
    return [d.path for d in diags]


def _ok_pytest() -> dict:
    return {"name": "shell", "args": "pytest tests/unit -q", "ok": True}


# --- bound: a successful runner call clears every measured claim ----------


def test_bound_measured_no_diags() -> None:
    findings = [{"evidence": "measured"}, {"evidence": "measured"}]
    assert _run(findings, tool_calls=[_ok_pytest()]) == []


def test_bound_args_as_dict() -> None:
    call = {"name": "powershell", "args": {"command": "dotnet test ./Foo"}, "ok": True}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == []


# --- unbound: measured claims are flagged --------------------------------


def test_unbound_no_tool_calls_flags_measured() -> None:
    assert _run([{"evidence": "measured"}]) == ["findings[0].evidence"]


def test_unbound_empty_tool_calls_flags_measured() -> None:
    assert _run([{"evidence": "measured"}], tool_calls=[]) == ["findings[0].evidence"]


def test_unbound_flags_only_measured_findings() -> None:
    findings = [
        {"evidence": "inferred"},
        {"evidence": "measured"},
        {"evidence": "measured"},
    ]
    assert _run(findings) == ["findings[1].evidence", "findings[2].evidence"]


# --- calls that must NOT bind ---------------------------------------------


def test_failed_runner_call_does_not_bind() -> None:
    call = {"name": "shell", "args": "pytest tests/", "ok": False, "error": "boom"}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


def test_ok_missing_does_not_bind() -> None:
    call = {"name": "shell", "args": "pytest tests/"}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


def test_non_runner_tool_does_not_bind() -> None:
    call = {"name": "read", "args": "pytest tests/", "ok": True}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


def test_runner_tool_non_runner_command_does_not_bind() -> None:
    call = {"name": "shell", "args": "ls -la", "ok": True}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


@pytest.mark.parametrize(
    "command",
    [
        "Get-Content pytest.ini",
        "Write-Output pytest",
        "Get-Content coverage.xml",
    ],
)
def test_runner_metadata_or_file_names_do_not_bind(command) -> None:
    call = {
        "name": "powershell",
        "args": {"command": command, "description": "Run pytest coverage"},
        "ok": True,
    }
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


def test_ok_truthy_but_not_true_does_not_bind() -> None:
    # bool-safe: only ``ok is True`` binds, not truthy strings.
    call = {"name": "shell", "args": "pytest tests/", "ok": "yes"}
    assert _run([{"evidence": "measured"}], tool_calls=[call]) == ["findings[0].evidence"]


# --- inferred never flagged, even unbound ---------------------------------


def test_inferred_never_flagged() -> None:
    assert _run([{"evidence": "inferred"}, {"evidence": "inferred"}]) == []


def test_missing_evidence_treated_as_not_measured() -> None:
    assert _run([{"reasoning": "x"}]) == []


# --- config-owned overrides ------------------------------------------------


def test_custom_runner_pattern_binds() -> None:
    call = {"name": "shell", "args": "bazel test //pkg:all", "ok": True}
    diags = _run(
        [{"evidence": "measured"}],
        tool_calls=[call],
        runner_patterns=[r"bazel\s+test"],
    )
    assert diags == []


def test_field_name_agnostic_via_requires() -> None:
    findings = [{"basis": "measured"}]
    assert _run(findings, requires=["findings[].basis"]) == ["findings[0].basis"]


@pytest.mark.parametrize(
    ("results", "expected_attempts", "expected_evidence"),
    [
        (
            [
                _Result(_redgreen_output("measured")),
                _Result(_redgreen_output("inferred")),
            ],
            2,
            "inferred",
        ),
        (
            [_Result(_redgreen_output("measured"), tool_calls=[_ok_pytest()])],
            1,
            "measured",
        ),
    ],
)
def test_canonical_output_contains_measured_only_after_attestation(
    results, expected_attempts, expected_evidence
) -> None:
    outcome = run_agent_with_ovg(
        configuration=_CONFIG,
        agent="redgreen",
        context="ctx",
        run_fn=_runner(results),
        max_attempts=2,
    )

    assert outcome.valid is True
    assert outcome.attempts == expected_attempts
    assert json.loads(outcome.response)["findings"][0]["evidence"] == expected_evidence
