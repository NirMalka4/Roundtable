from roundtable.backend.sdk.tool_policy import (
    enforce_tool_deadlines,
    resolve_execution_policy,
    tool_deadline_hooks,
)
from roundtable.graph import (
    PowershellToolPolicy,
    ReadPowershellToolPolicy,
    ToolPolicy,
)


def _declared_policy() -> ToolPolicy:
    return ToolPolicy(
        powershell=PowershellToolPolicy(
            invocation_cap_seconds=120,
            detached_allowed=False,
        ),
        read_powershell=ReadPowershellToolPolicy(poll_cap_seconds=30),
    )


def test_resolved_execution_policy_exposes_actual_backend_limits() -> None:
    resolved = resolve_execution_policy(_declared_policy())
    assert resolved is not None
    assert resolved.to_dict() == {
        "shellInvocationCapSeconds": 120.0,
        "backgroundPollCapSeconds": 30.0,
        "detachedAllowed": False,
    }


def test_absent_tool_policy_resolves_to_no_execution_policy() -> None:
    assert resolve_execution_policy(None) is None


def test_tool_deadline_caps_shell_and_background_wait_without_changing_command() -> None:
    policy = resolve_execution_policy(_declared_policy())
    assert policy is not None
    shell = enforce_tool_deadlines(
        {
            "toolName": "powershell",
            "toolArgs": {"command": "pytest tests/unit/test_x.py", "initial_wait": 600},
        },
        {},
        policy=policy,
    )
    poll = enforce_tool_deadlines(
        {"toolName": "read_powershell", "toolArgs": {"shellId": "s", "delay": 120}},
        {},
        policy=policy,
    )
    assert shell == {
        "modifiedArgs": {
            "command": "pytest tests/unit/test_x.py",
            "initial_wait": 120,
        }
    }
    assert poll == {"modifiedArgs": {"shellId": "s", "delay": 30}}


def test_tool_deadline_leaves_bounded_and_non_shell_calls_unchanged() -> None:
    policy = resolve_execution_policy(_declared_policy())
    assert policy is not None
    assert (
        enforce_tool_deadlines(
            {"toolName": "powershell", "toolArgs": {"command": "x", "initial_wait": 30}},
            {},
            policy=policy,
        )
        is None
    )
    assert (
        enforce_tool_deadlines(
            {"toolName": "view", "toolArgs": {"path": "README.md"}},
            {},
            policy=policy,
        )
        is None
    )


def test_tool_deadline_forces_declared_shell_to_remain_attached() -> None:
    policy = resolve_execution_policy(_declared_policy())
    assert policy is not None
    assert enforce_tool_deadlines(
        {
            "toolName": "powershell",
            "toolArgs": {"command": "pytest tests/unit/test_x.py", "detach": True},
        },
        {},
        policy=policy,
    ) == {
        "modifiedArgs": {
            "command": "pytest tests/unit/test_x.py",
            "detach": False,
        }
    }


def test_tool_deadline_hook_caps_wait_to_invocation_limit() -> None:
    policy = resolve_execution_policy(_declared_policy())
    assert policy is not None
    hook = tool_deadline_hooks(policy)["on_pre_tool_use"]
    assert hook(
        {"toolName": "powershell", "toolArgs": {"command": "pytest", "initial_wait": 600}},
        {},
    ) == {"modifiedArgs": {"command": "pytest", "initial_wait": 120}}


def test_partial_poll_policy_does_not_modify_powershell() -> None:
    policy = resolve_execution_policy(
        ToolPolicy(read_powershell=ReadPowershellToolPolicy(poll_cap_seconds=10))
    )
    assert policy is not None
    hook = tool_deadline_hooks(policy)["on_pre_tool_use"]
    assert (
        hook(
            {"toolName": "powershell", "toolArgs": {"command": "pytest", "initial_wait": 600}},
            {},
        )
        is None
    )
