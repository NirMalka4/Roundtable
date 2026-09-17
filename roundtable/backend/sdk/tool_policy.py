"""SDK pre-tool policy for bounded shell/test waits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from roundtable.graph import ToolPolicy

from ..result import ExecutionPolicy


def resolve_execution_policy(policy: ToolPolicy | None) -> ExecutionPolicy | None:
    """Resolve graph-owned SDK-native limits into backend telemetry."""
    if policy is None:
        return None
    return ExecutionPolicy(
        shell_invocation_cap_s=(
            float(policy.powershell.invocation_cap_seconds) if policy.powershell else None
        ),
        background_poll_cap_s=(
            float(policy.read_powershell.poll_cap_seconds) if policy.read_powershell else None
        ),
        detached_allowed=(policy.powershell.detached_allowed if policy.powershell else None),
    )


def _bounded(value: Any, limit: float) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(min(value, limit)))


def enforce_tool_deadlines(
    input_data: Mapping[str, Any],
    _context: dict[str, str],
    *,
    policy: ExecutionPolicy,
) -> dict | None:
    """Cap one PowerShell invocation/poll via the SDK's supported pre-tool hook."""
    name = str(input_data.get("toolName") or "").lower()
    args = input_data.get("toolArgs")
    if not isinstance(args, Mapping):
        return None
    modified = dict(args)
    changed = False
    if name == "powershell":
        if policy.detached_allowed is False and args.get("detach") is True:
            modified["detach"] = False
            changed = True
        if policy.shell_invocation_cap_s is None:
            return {"modifiedArgs": modified} if changed else None
        bounded = _bounded(args.get("initial_wait"), policy.shell_invocation_cap_s)
        if bounded is not None and bounded != args.get("initial_wait"):
            modified["initial_wait"] = bounded
            changed = True
    elif name == "read_powershell":
        if policy.background_poll_cap_s is None:
            return None
        bounded = _bounded(args.get("delay"), policy.background_poll_cap_s)
        if bounded is not None and bounded != args.get("delay"):
            modified["delay"] = bounded
            changed = True
    else:
        return None
    return {"modifiedArgs": modified} if changed else None


def tool_deadline_hooks(policy: ExecutionPolicy) -> dict[str, Any]:
    def enforce(input_data: Mapping[str, Any], context: dict[str, str]) -> dict | None:
        return enforce_tool_deadlines(
            input_data,
            context,
            policy=policy,
        )

    return {"on_pre_tool_use": enforce}
