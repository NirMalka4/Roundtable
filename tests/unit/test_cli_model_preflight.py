"""Automatic authenticated capability preflight wiring for doctor and review."""

from __future__ import annotations

from argparse import SUPPRESS, Namespace

import pytest

from roundtable import capabilities, cli
from roundtable.capabilities import RuntimeCapabilities
from roundtable.decision import EXIT_ERROR
from roundtable.runtime.agent_setup import ValidationReport


def _capabilities() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        model_ids=frozenset({"gpt-5.6-sol"}),
        builtin_tool_ids=frozenset({"view"}),
        always_on_tool_ids=frozenset(),
        cli_version="1.0.84-1",
        sdk_package_version="1.0.10rc1",
        sdk_protocol_version="3",
        tool_source="cache",
    )


def test_normal_doctor_resolves_once_and_validates_same_capabilities(monkeypatch) -> None:
    resolved = _capabilities()
    calls = 0
    validated: list[object] = []

    def resolve() -> RuntimeCapabilities:
        nonlocal calls
        calls += 1
        return resolved

    monkeypatch.setattr(capabilities, "resolve_runtime_capabilities", resolve)
    monkeypatch.setattr(
        cli,
        "validate_agents",
        lambda *_args, **kwargs: (
            validated.append(kwargs["runtime_capabilities"]) or ValidationReport()
        ),
    )

    assert cli._cmd_doctor(Namespace(static=False)) == 0
    assert calls == 1
    assert validated == [resolved]


def test_internal_static_doctor_never_resolves_live_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(
        capabilities,
        "resolve_runtime_capabilities",
        lambda: pytest.fail("static doctor must not perform live I/O"),
    )
    validated: list[object] = []
    monkeypatch.setattr(
        cli,
        "validate_agents",
        lambda *_args, **kwargs: (
            validated.append(kwargs["runtime_capabilities"]) or ValidationReport()
        ),
    )

    assert cli._cmd_doctor(Namespace(static=True)) == 0
    assert validated == [None]


def test_normal_doctor_fails_closed_when_discovery_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        capabilities,
        "resolve_runtime_capabilities",
        lambda: (_ for _ in ()).throw(RuntimeError("auth unavailable")),
    )
    monkeypatch.setattr(
        cli,
        "validate_agents",
        lambda *_args, **_kwargs: pytest.fail("validation cannot use stale capability data"),
    )
    assert cli._cmd_doctor(Namespace(static=False)) == EXIT_ERROR
    assert cli._cmd_doctor(Namespace(static=False)) == EXIT_ERROR


def _review_args(*, backend: str | None, simulate: bool, dry_run: bool) -> Namespace:
    return Namespace(
        backend=backend,
        simulate=simulate,
        dry_run=dry_run,
        input_from=None,
        pr=None,
        repo=None,
        base_branch=None,
    )


@pytest.mark.parametrize(
    ("args", "expected_resolutions", "expects_live_capabilities"),
    [
        (_review_args(backend=None, simulate=False, dry_run=False), 1, True),
        (_review_args(backend=None, simulate=False, dry_run=True), 0, False),
        (_review_args(backend=None, simulate=True, dry_run=False), 0, False),
        (_review_args(backend="mock", simulate=False, dry_run=False), 0, False),
    ],
)
def test_only_real_copilot_review_uses_live_capability_preflight(
    monkeypatch,
    args: Namespace,
    expected_resolutions: int,
    expects_live_capabilities: bool,
) -> None:
    resolved = _capabilities()
    resolutions = 0
    preflight_capabilities: list[object] = []

    def resolve() -> RuntimeCapabilities:
        nonlocal resolutions
        resolutions += 1
        return resolved

    def stop_before_workspace(_configuration, runtime_capabilities=None) -> str:
        preflight_capabilities.append(runtime_capabilities)
        return "deliberate graph stop"

    monkeypatch.setattr(capabilities, "resolve_runtime_capabilities", resolve)
    monkeypatch.setattr(cli, "_preflight_graph", stop_before_workspace)

    assert cli._cmd_review(args) == EXIT_ERROR
    assert resolutions == expected_resolutions
    assert preflight_capabilities == [resolved if expects_live_capabilities else None]


def test_real_copilot_review_fails_discovery_before_graph_or_workspace(monkeypatch) -> None:
    monkeypatch.setattr(
        capabilities,
        "resolve_runtime_capabilities",
        lambda: (_ for _ in ()).throw(RuntimeError("not signed in")),
    )
    monkeypatch.setattr(
        cli,
        "_preflight_graph",
        lambda *_args: pytest.fail("failed discovery must stop before graph validation"),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_review_target",
        lambda *_args: pytest.fail("failed discovery must stop before workspace resolution"),
    )
    args = _review_args(backend=None, simulate=False, dry_run=False)
    assert cli._cmd_review(args) == EXIT_ERROR
    assert cli._cmd_review(args) == EXIT_ERROR


def test_live_graph_incompatibility_stops_before_workspace(monkeypatch) -> None:
    monkeypatch.setattr(capabilities, "resolve_runtime_capabilities", _capabilities)
    monkeypatch.setattr(cli, "_preflight_graph", lambda *_args: "removed tool")
    monkeypatch.setattr(
        cli,
        "_resolve_review_target",
        lambda *_args: pytest.fail("invalid live graph must stop before workspace resolution"),
    )

    assert cli._cmd_review(_review_args(backend=None, simulate=False, dry_run=False)) == EXIT_ERROR


def test_static_and_maintenance_switches_are_hidden_from_help() -> None:
    parser = cli.build_parser()
    subparsers = parser._subparsers._group_actions[0].choices
    doctor_actions = {action.dest: action for action in subparsers["doctor"]._actions}
    tools_actions = {action.dest: action for action in subparsers["tools"]._actions}

    assert doctor_actions["static"].help == SUPPRESS
    assert tools_actions["refresh_inventory"].help == SUPPRESS
    with pytest.raises(SystemExit):
        parser.parse_args(["doctor", "--offline"])
