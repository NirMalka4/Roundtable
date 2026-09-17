"""``roundtable tools`` — the granted-versus-used tool surface, and its cross-run diff.

The regression this exists for changed the SDK's tool surface without touching a
single ``tools:`` declaration, so these tests pin the *observed* side hardest:
the diff must fire on a composition move even when the grant is byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.reporting.snapshot import from_payload, to_payload
from roundtable.reporting.tool_surface import (
    AgentToolSurface,
    diff_tool_surface,
    read_tool_surface,
)


def _session(
    root: Path,
    *,
    tool_calls: list[str],
    declared: list[str] | None = None,
    declared_mcp: list[str] | None = None,
    inventory: list[str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    agent_trace: dict = {"agent": "redgreen", "valid": True, "attempts": 1}
    (root / "trace.json").write_text(
        json.dumps(
            {
                "sessionId": root.name,
                "verdict": "APPROVE",
                "agents": [agent_trace],
            }
        ),
        encoding="utf-8",
    )
    (root / "usage-summary.json").write_text(
        json.dumps(
            {
                "perAgent": [
                    {
                        "agent": "redgreen",
                        "valid": True,
                        "attempts": 1,
                        "attemptsDetail": [
                            {
                                "attempt": 1,
                                "outcome": "valid",
                                "toolCalls": [{"name": n} for n in tool_calls],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    agents: list[dict] = [{"key": "redgreen", "runtime": "llm"}]
    if declared is not None:
        agents[0]["declaredTools"] = declared
    if declared_mcp is not None:
        agents[0]["declaredMcpTools"] = declared_mcp
    graph: dict = {"version": 1, "agents": agents}
    if inventory is not None:
        graph["mcpToolInventory"] = inventory
    (root / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return root


def test_grant_and_usage_are_both_readable_from_the_artifacts(tmp_path: Path) -> None:
    surface = read_tool_surface(
        _session(tmp_path / "s", tool_calls=["view", "view", "powershell"], declared=["read"])
    )
    assert surface["redgreen"].declared == ("read",)
    assert surface["redgreen"].used == {"view": 2, "powershell": 1}
    assert surface["redgreen"].total_calls == 3


def test_a_run_without_a_recorded_grant_says_unknown_not_nothing(tmp_path: Path) -> None:
    """Legacy sessions predate ``declaredTools``; absent must not read as 'no grant'."""
    surface = read_tool_surface(_session(tmp_path / "s", tool_calls=["view"]))
    assert surface["redgreen"].declared == ()
    assert surface["redgreen"].grant_known is False


def test_the_diff_fires_on_a_composition_move_under_an_identical_grant(tmp_path: Path) -> None:
    """The b2d00ef shape: the declared value never changed, the tools did."""
    before = read_tool_surface(
        _session(tmp_path / "a", tool_calls=["grep", "glob"], declared=["read", "search"])
    )
    after = read_tool_surface(
        _session(tmp_path / "b", tool_calls=["powershell"] * 9, declared=["read", "search"])
    )
    (delta,) = diff_tool_surface(before, after)
    assert delta.shifted
    assert delta.added == ("powershell",)
    assert delta.removed == ("glob", "grep")
    assert (delta.calls_before, delta.calls_after) == (2, 9)


def test_a_steady_surface_reports_no_shift(tmp_path: Path) -> None:
    before = read_tool_surface(_session(tmp_path / "a", tool_calls=["view", "view"]))
    after = read_tool_surface(_session(tmp_path / "b", tool_calls=["view"]))
    (delta,) = diff_tool_surface(before, after)
    assert not delta.shifted


def test_an_agent_missing_from_one_run_is_not_a_tool_shift() -> None:
    """It did not run — a coverage fact, reported elsewhere, not a surface move."""
    before = {"a": AgentToolSurface(key="a", used={"view": 1})}
    after = {
        "a": AgentToolSurface(key="a", used={"view": 1}),
        "b": AgentToolSurface(key="b", used={"powershell": 4}),
    }
    assert [d.key for d in diff_tool_surface(before, after)] == ["a"]


def test_the_grant_survives_the_snapshot_round_trip() -> None:
    from roundtable.reporting.snapshot import AgentInfo, GraphSnapshot

    snap = GraphSnapshot(agents={"x": AgentInfo(key="x", declared_tools=("read", "a-b/c_d"))})
    assert from_payload(to_payload(snap)).agents["x"].declared_tools == ("read", "a-b/c_d")


def test_a_missing_session_is_an_argument_error_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, OSError, ValueError)):
        read_tool_surface(tmp_path / "nope")


# ── MCP grant: the one part of the surface that IS ruled on ──────────────────

_MCP = ["srv-alpha", "srv-beta", "srv-gamma"]


def _mcp_session(root: Path, **kw) -> Path:
    return _session(root, inventory=_MCP, **kw)


def test_an_mcp_call_outside_the_agents_grant_is_a_violation(tmp_path: Path) -> None:
    surface = read_tool_surface(
        _mcp_session(
            tmp_path / "s",
            tool_calls=["view", "srv-beta"],
            declared=["view", "srv-alpha"],
            declared_mcp=["srv-alpha"],
        )
    )
    assert surface["redgreen"].used_outside_grant == ("srv-beta",)


def test_a_granted_mcp_call_is_not_a_violation(tmp_path: Path) -> None:
    surface = read_tool_surface(
        _mcp_session(
            tmp_path / "s",
            tool_calls=["srv-alpha"],
            declared=["srv-alpha"],
            declared_mcp=["srv-alpha"],
        )
    )
    assert surface["redgreen"].used_outside_grant == ()


def test_an_unrecorded_grant_accuses_nobody(tmp_path: Path) -> None:
    """``declared == ()`` also means *unrestricted*, so the verdict must stay silent."""
    surface = read_tool_surface(_mcp_session(tmp_path / "s", tool_calls=["srv-beta"]))
    s = surface["redgreen"]
    assert s.grant_known is False
    assert s.used_outside_grant == ()


def test_a_builtin_call_outside_the_grant_is_a_violation(tmp_path: Path) -> None:
    """Grants name concrete runtime tools, so built-ins are as rulable as MCP ones."""
    surface = read_tool_surface(
        _session(tmp_path / "s", tool_calls=["view", "powershell"], declared=["view"])
    )
    assert surface["redgreen"].used_outside_grant == ("powershell",)


def test_an_always_on_tool_is_never_a_violation(tmp_path: Path) -> None:
    """``sql`` survives ``tools: []``, so no grant could have stopped it."""
    surface = read_tool_surface(
        _session(tmp_path / "s", tool_calls=["view", "sql"], declared=["view"])
    )
    assert surface["redgreen"].used_outside_grant == ()


def test_a_granted_tool_the_run_never_reached_is_reported(tmp_path: Path) -> None:
    surface = read_tool_surface(
        _mcp_session(
            tmp_path / "s",
            tool_calls=["srv-alpha"],
            declared=["view", "srv-alpha", "srv-gamma"],
            declared_mcp=["srv-alpha", "srv-gamma"],
        )
    )
    assert surface["redgreen"].granted_unused == ("srv-gamma", "view")


def test_the_mcp_grant_survives_the_snapshot_round_trip() -> None:
    from roundtable.reporting.snapshot import AgentInfo, GraphSnapshot

    snap = GraphSnapshot(
        agents={"x": AgentInfo(key="x", declared_mcp_tools=("srv-alpha",))},
        mcp_tool_inventory=("srv-alpha", "srv-beta"),
    )
    back = from_payload(to_payload(snap))
    assert back.agents["x"].declared_mcp_tools == ("srv-alpha",)
    assert back.mcp_tool_inventory == ("srv-alpha", "srv-beta")


# ── the resolved middle term ─────────────────────────────────────────────────


def test_the_runtimes_resolved_grant_makes_unused_builtins_answerable(tmp_path: Path) -> None:
    """Concrete grants answer this directly — no runtime-reported middle term needed."""
    surface = read_tool_surface(
        _session(
            tmp_path / "s",
            tool_calls=["view", "view"],
            declared=["view", "grep", "powershell"],
        )
    )
    assert surface["redgreen"].granted_unused == ("grep", "powershell")


def test_a_call_outside_the_grant_is_surfaced(tmp_path: Path) -> None:
    surface = read_tool_surface(
        _session(
            tmp_path / "s",
            tool_calls=["task"],
            declared=["view"],
        )
    )
    assert surface["redgreen"].used_outside_grant == ("task",)


def test_a_run_with_no_grant_recorded_stays_silent(tmp_path: Path) -> None:
    """A legacy session records no grant, which must not read as "granted nothing"."""
    surface = read_tool_surface(_session(tmp_path / "s", tool_calls=["view"]))
    s = surface["redgreen"]
    assert s.grant_known is False
    assert s.used_outside_grant == ()
    assert s.granted_unused == ()
