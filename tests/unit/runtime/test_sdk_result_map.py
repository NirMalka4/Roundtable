"""Unit tests for the SDK result mapper (synthetic events, no SDK import)."""

from __future__ import annotations

from types import SimpleNamespace

from roundtable.backend.sdk.result_map import map_result_fields


def _msg(content, tool_requests=None):
    return SimpleNamespace(
        type="assistant.message",
        data=SimpleNamespace(content=content, tool_requests=tool_requests),
    )


def _tr(name, arguments, tool_call_id=None):
    return SimpleNamespace(name=name, arguments=arguments, tool_call_id=tool_call_id)


def _complete(tool_call_id, success, error=None):
    return SimpleNamespace(
        type="tool.execution_complete",
        data=SimpleNamespace(
            tool_call_id=tool_call_id,
            success=success,
            error=SimpleNamespace(**error) if error else None,
        ),
    )


def test_final_content_is_last_nonempty_message() -> None:
    events = [_msg("first"), _msg("second"), _msg("")]
    mapped = map_result_fields(events)
    assert mapped.final_content == "second"
    assert mapped.rounds == 3  # all assistant.message events count as rounds


def test_tool_calls_collected_in_order() -> None:
    events = [
        _msg("thinking", [_tr("view", {"path": "a"}), _tr("grep", {"q": "x"})]),
        _msg("done", [_tr("view", {"path": "b"})]),
    ]
    mapped = map_result_fields(events)
    assert mapped.tools_used == ["view", "grep", "view"]
    assert mapped.tool_call_count == 3
    assert mapped.tool_calls[0] == {"name": "view", "args": {"path": "a"}}
    assert mapped.tool_calls[2] == {"name": "view", "args": {"path": "b"}}


def test_submission_transport_is_not_counted_as_an_ordinary_tool_or_payload() -> None:
    events = [
        _msg(
            "",
            [
                _tr("view", {"path": "a"}),
                _tr("roundtable_submit_output", {"output": {"large": "payload"}}),
            ],
        )
    ]
    mapped = map_result_fields(events)
    assert mapped.tools_used == ["view"]
    assert mapped.tool_calls == [{"name": "view", "args": {"path": "a"}}]


def test_non_assistant_events_ignored() -> None:
    events = [
        SimpleNamespace(type="session.start", data=None),
        _msg("answer"),
        SimpleNamespace(type="assistant.usage", data=SimpleNamespace(output_tokens=5)),
    ]
    mapped = map_result_fields(events)
    assert mapped.final_content == "answer"
    assert mapped.rounds == 1


def test_content_list_blocks_joined() -> None:
    events = [_msg([{"text": "hello "}, {"text": "world"}])]
    assert map_result_fields(events).final_content == "hello world"


def test_empty_events() -> None:
    mapped = map_result_fields([])
    assert mapped.final_content == ""
    assert mapped.rounds == 0
    assert mapped.tool_call_count == 0


def test_tool_request_without_name_skipped() -> None:
    events = [_msg("x", [_tr(None, {}), _tr("view", {})])]
    mapped = map_result_fields(events)
    assert mapped.tools_used == ["view"]


def test_tool_call_outcome_joined_by_id() -> None:
    events = [
        _msg("t", [_tr("view", {"path": "a"}, "c1"), _tr("ado-pr", {"id": 2}, "c2")]),
        _complete("c1", success=True),
        _complete(
            "c2",
            success=False,
            error={
                "message": "boom",
                "source": "sdk-host",
                "type": "ToolTimeout",
                "code": "TOOL_TIMEOUT",
                "timeout_owner": "builtin-tool",
            },
        ),
    ]
    mapped = map_result_fields(events)
    assert mapped.tool_calls[0] == {"name": "view", "args": {"path": "a"}, "ok": True}
    assert mapped.tool_calls[1] == {
        "name": "ado-pr",
        "args": {"id": 2},
        "ok": False,
        "toolError": {
            "source": "sdk-host",
            "type": "ToolTimeout",
            "code": "TOOL_TIMEOUT",
            "timeoutOwner": "builtin-tool",
            "message": "boom",
        },
    }


def test_tool_call_without_completion_has_no_outcome() -> None:
    # A request with no matching completion event keeps neither ok nor error.
    events = [_msg("t", [_tr("view", {"path": "a"}, "c1")])]
    mapped = map_result_fields(events)
    assert mapped.tool_calls[0] == {"name": "view", "args": {"path": "a"}}


def test_successful_tool_call_carries_no_tool_error() -> None:
    events = [_msg("t", [_tr("view", {"path": "a"}, "c1")]), _complete("c1", success=True)]
    assert "toolError" not in map_result_fields(events).tool_calls[0]


def test_sdk_message_only_error_does_not_invent_provenance() -> None:
    events = [
        _msg("t", [_tr("rg", {"pattern": "x"}, "c1")]),
        _complete("c1", success=False, error={"message": "timeout"}),
    ]
    assert map_result_fields(events).tool_calls[0]["toolError"] == {"message": "timeout"}
