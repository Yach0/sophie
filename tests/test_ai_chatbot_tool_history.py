from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from sophie_bot.modules.ai.utils.chatbot_tool_history import (
    collect_tool_call_ids,
    extract_tool_exchanges,
)


def _run_with_tool_call(
    tool_name: str = "web_search", tool_call_id: str = "call-1"
) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content="who won?")]),
        ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"query": "who won"}, tool_call_id=tool_call_id)]),
        ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content="they did", tool_call_id=tool_call_id)]),
        ModelResponse(parts=[TextPart(content="They did.")]),
    ]


def test_extract_keeps_complete_call_and_return_pairs() -> None:
    exchanges = extract_tool_exchanges(_run_with_tool_call(), max_content_chars=100)

    assert [type(message) for message in exchanges] == [ModelResponse, ModelRequest]
    assert isinstance(exchanges[0].parts[0], ToolCallPart)
    assert isinstance(exchanges[1].parts[0], ToolReturnPart)
    assert exchanges[1].parts[0].content == "they did"


def test_extract_drops_call_without_return() -> None:
    messages = _run_with_tool_call()
    del messages[2]

    assert extract_tool_exchanges(messages, max_content_chars=100) == []


def test_extract_skips_memory_tools() -> None:
    messages = _run_with_tool_call(tool_name="write_memory")

    assert extract_tool_exchanges(messages, max_content_chars=100) == []


def test_extract_skips_already_replayed_call_ids() -> None:
    messages = _run_with_tool_call()
    skipped = collect_tool_call_ids(messages[1:3])

    assert extract_tool_exchanges(messages, max_content_chars=100, skip_tool_call_ids=skipped) == []


def test_extract_truncates_long_tool_output() -> None:
    messages = _run_with_tool_call()
    messages[2] = ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="x" * 50, tool_call_id="call-1")])

    exchanges = extract_tool_exchanges(messages, max_content_chars=10)

    assert exchanges[1].parts[0].content == "x" * 10 + "... [truncated]"
