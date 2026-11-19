from __future__ import annotations

import json
from collections.abc import Sequence
from unittest.mock import MagicMock

import httpx2
import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelHTTPError, ModelRetry
from pydantic_ai.messages import (
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.ai_tokens import ModelTokenBudget, TokenCounter
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


@pytest.fixture(autouse=True)
def no_external_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


def completion(model_name: str, message: dict[str, object], streaming: bool) -> httpx2.Response:
    payload = {
        "id": "offline",
        "created": 0,
        "model": model_name,
        "object": "chat.completion.chunk" if streaming else "chat.completion",
        "choices": [
            {
                "index": 0,
                "delta" if streaming else "message": message,
                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
            }
        ],
    }
    if streaming:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
        )
    return httpx2.Response(200, json=payload)


@pytest.mark.parametrize("streaming", [False, True])
async def test_sdk_retries_only_the_failed_request_after_mutation(streaming: bool) -> None:
    payloads: list[dict[str, object]] = []
    mutations: list[int] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 2:
            return httpx2.Response(503, headers={"retry-after-ms": "1"}, json={"error": {"message": "unavailable"}})
        if len(payloads) == 1:
            call = {"id": "mutation", "type": "function", "function": {"name": "mutate", "arguments": '{"amount":1}'}}
            if streaming:
                call["index"] = 0
            return completion("offline", {"role": "assistant", "tool_calls": [call]}, streaming)
        assert any(
            message["role"] == "tool" and message["content"] == "mutation completed" for message in payload["messages"]
        )
        return completion("offline", {"role": "assistant", "content": "done"}, streaming)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(model)

        @agent.tool_plain
        async def mutate(amount: int) -> str:
            mutations.append(amount)
            return "mutation completed"

        async def on_text(text: str) -> None:
            pass

        if streaming:
            result = await ai_run.run_ai_stream(agent, "make the change", on_text)
        else:
            result = await ai_run.run_ai_text(agent, "make the change")

    assert result.output == "done"
    assert mutations == [1]
    assert len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert result.usage.requests == 2


async def test_each_candidate_gets_its_settings_and_its_own_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, object]] = []
    reported: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ai_run, "track_ai_usage", lambda model, usage: reported.append((model.model_name, usage.input_tokens))
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["model"] == "primary":
            return httpx2.Response(400, json={"error": {"message": "unsupported"}})
        return completion("backup", {"role": "assistant", "content": "done"}, False)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(api_key="offline", http_client=client)
        primary = OpenAIChatModel("primary", provider=provider, settings={"temperature": 0.1, "max_tokens": 31})
        backup = OpenAIChatModel("backup", provider=provider, settings={"temperature": 0.7, "max_tokens": 17})
        plan = AIModelPlan(
            (
                AIModelCandidate(primary, "primary", service_tier="flex"),
                AIModelCandidate(backup, "backup", service_tier="none"),
            )
        )
        result = await ai_run.run_ai_text(
            Agent(primary), "hello", model_plan=plan, request_options=AIRequestOptions(service_tier="default")
        )

    assert result.served_model is backup
    assert [(payload["temperature"], payload["max_completion_tokens"]) for payload in payloads] == [
        (0.1, 31),
        (0.7, 17),
    ]
    assert payloads[0]["service_tier"] == "flex"
    assert "service_tier" not in payloads[1]
    assert reported == [("backup", 0)]


@pytest.mark.parametrize("adjacent_history", [False, True])
async def test_context_projection_keeps_live_tool_results_on_fallback(adjacent_history: bool) -> None:
    projections: list[tuple[str, list[ModelRequest | ModelResponse]]] = []
    mutations: list[int] = []

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelRequest | ModelResponse]
    ) -> tuple[list[UserContent], list[ModelRequest | ModelResponse]]:
        projections.append((candidate.model_name, list(continuation)))
        return ["current request"], [ModelRequest(parts=[UserPromptPart(candidate.model_name + " history")])]

    def primary_request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            raise ModelHTTPError(400, "primary", body="unsupported")
        return ModelResponse(parts=[ToolCallPart("mutate", {"amount": 1})], usage=RequestUsage(input_tokens=3))

    def backup_request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert messages[0].parts[0].content == "backup history"
        assert any(
            isinstance(part, ToolReturnPart) and part.content == "completed"
            for message in messages
            for part in message.parts
        )
        return ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=5))

    primary = FunctionModel(primary_request, model_name="primary")
    backup = FunctionModel(backup_request, model_name="backup")
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "completed"

    plan = AIModelPlan((AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    result = await ai_run.run_ai_text(
        agent,
        "current request",
        message_history=[ModelRequest(parts=[UserPromptPart("old")]), ModelRequest(parts=[UserPromptPart("context")])]
        if adjacent_history
        else None,
        model_plan=plan,
        request_options=AIRequestOptions(context_projection=project),
    )
    assert result.output == "done"
    assert mutations == [1]
    assert result.usage.input_tokens == 8
    assert projections[-1][0] == "backup"
    assert len(projections[-1][1]) == 2


async def test_context_reserves_space_for_live_tool_messages() -> None:
    counter = TokenCounter("unknown")
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history.prompt = ["current"]
    budget = ModelTokenBudget(counter, 1000)
    history._token_budgets = (budget,)
    history._budget = budget.input_tokens
    candidate = AIModelCandidate(
        FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])), "unknown"
    )
    continuation = [ModelResponse(parts=[TextPart("tool output " * 2000)])]
    with pytest.raises(ValueError, match="exceeds its token budget"):
        history.project_for_candidate(candidate, continuation)
    assert history.prompt == ["current"]
    assert history._budget == 1000


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("history_shape", ["adjacent", "orphan", "normal"])
@pytest.mark.parametrize("sequence_prompt", [False, True])
async def test_projection_uses_normalized_run_boundary(
    streaming: bool, history_shape: str, sequence_prompt: bool
) -> None:
    system = SystemPromptPart("historical system instruction")
    history: list[ModelMessage] = [
        ModelRequest(parts=[system, UserPromptPart("old question")]),
        ModelRequest(parts=[UserPromptPart("old context")]),
    ]
    if history_shape == "orphan":
        history.insert(1, ModelRequest(parts=[ToolReturnPart("missing", "orphan result", "orphan")]))
    elif history_shape == "normal":
        history.insert(1, ModelResponse(parts=[TextPart("old answer")]))
    projected_history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("projected question")]),
        ModelResponse(parts=[ToolCallPart("lookup", {}, "historical-call")]),
        ModelRequest(parts=[ToolReturnPart("lookup", "historical result", "historical-call")]),
    ]
    projections: list[list[ModelMessage]] = []
    payloads: list[dict[str, object]] = []

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelMessage]
    ) -> tuple[list[UserContent], list[ModelMessage]]:
        projections.append(list(continuation))
        return ["projected current context"], projected_history

    def add_current_parts(ctx: RunContext[object], request: ModelRequestContext) -> ModelRequestContext:
        current = request.messages[-1]
        assert isinstance(current, ModelRequest)
        current.parts.extend([SystemPromptPart("current system"), UserPromptPart("extra user context")])
        return request

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        return completion("offline", {"role": "assistant", "content": "done"}, streaming)

    async def on_text(text: str) -> None:
        assert text == "done"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(model, capabilities=[Hooks(before_model_request=add_current_parts)])
        options = AIRequestOptions(context_projection=project)
        prompt: str | list[UserContent] = ["current prompt"] if sequence_prompt else "current prompt"
        if streaming:
            result = await ai_run.run_ai_stream(
                agent, prompt, on_text, message_history=history, request_options=options
            )
        else:
            result = await ai_run.run_ai_text(agent, prompt, message_history=history, request_options=options)

    assert result.output == "done"
    assert projections == [[]]
    assert len(payloads) == 1
    wire = json.dumps(payloads[0]["messages"])
    for expected in (
        "historical system instruction",
        "current system",
        "extra user context",
        "projected current context",
        "historical result",
    ):
        assert wire.count(expected) == 1
    for removed in ("old question", "old context", "old answer", "orphan result", "current prompt"):
        assert removed not in wire
    tool_index = next(index for index, message in enumerate(payloads[0]["messages"]) if message["role"] == "tool")
    assert payloads[0]["messages"][tool_index - 1]["tool_calls"][0]["id"] == "historical-call"


@pytest.mark.parametrize("projection", [False, True])
async def test_stream_boundary_keeps_earlier_live_text_after_history_cleanup(
    monkeypatch: pytest.MonkeyPatch, projection: bool
) -> None:
    monkeypatch.setattr(ai_run, "_STREAM_DEBOUNCE_SECONDS", 0.0)
    updates: list[str] = []
    payloads: list[dict[str, object]] = []
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("old question")]),
        ModelResponse(parts=[TextPart("old answer")]),
        *[ModelRequest(parts=[ToolReturnPart("missing", "orphan", str(index))]) for index in range(6)],
    ]

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelMessage]
    ) -> tuple[list[UserContent], list[ModelMessage]]:
        return ["current prompt"], history[:2]

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            call = {
                "index": 0,
                "id": "live-call",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
            return completion("offline", {"role": "assistant", "content": "working ", "tool_calls": [call]}, True)
        return completion("offline", {"role": "assistant", "content": "done"}, True)

    async def on_text(text: str) -> None:
        updates.append(text)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(model)

        @agent.tool_plain
        async def lookup() -> str:
            return "live result"

        result = await ai_run.run_ai_stream(
            agent,
            "current prompt",
            on_text,
            message_history=history,
            request_options=AIRequestOptions(context_projection=project) if projection else None,
        )

    assert result.output == "working done"
    assert updates == ["working ", "working done"]
    assert len(payloads) == 2
    for payload in payloads:
        wire = json.dumps(payload["messages"])
        assert wire.count("current prompt") == 1
        assert "orphan" not in wire


async def test_projection_preserves_validation_retry_and_already_projected_system() -> None:
    system = SystemPromptPart("system instruction")
    history = [
        ModelRequest(parts=[system, UserPromptPart("old")], run_id="previous-run"),
        ModelRequest(parts=[UserPromptPart("context")], run_id="previous-run"),
    ]
    continuations: list[list[ModelMessage]] = []
    validations: list[str] = []

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelMessage]
    ) -> tuple[list[UserContent], list[ModelMessage]]:
        continuations.append(list(continuation))
        return ["projected prompt"], [ModelRequest(parts=[system, UserPromptPart("projected history")])]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        parts = [part for message in messages for part in message.parts]
        assert parts.count(system) == 1
        assert sum(isinstance(part, UserPromptPart) and part.content == ["projected prompt"] for part in parts) == 1
        return ModelResponse(parts=[TextPart("done" if validations else "try again")])

    agent = Agent(FunctionModel(respond))

    @agent.output_validator
    async def validate(output: str) -> str:
        validations.append(output)
        if len(validations) == 1:
            raise ModelRetry("answer again")
        return output

    result = await ai_run.run_ai_text(
        agent, "current prompt", message_history=history, request_options=AIRequestOptions(context_projection=project)
    )
    assert result.output == "try againdone"
    assert validations == ["try again", "done"]
    assert [len(continuation) for continuation in continuations] == [0, 2]
    assert isinstance(continuations[1][0], ModelResponse)
    assert isinstance(continuations[1][1], ModelRequest)
    assert "answer again" in str(continuations[1][1].parts)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("projection", [False, True])
@pytest.mark.parametrize("retain_history", [False, True])
async def test_projection_does_not_revive_expired_request_system_prompts(
    streaming: bool, projection: bool, retain_history: bool
) -> None:
    history: list[ModelMessage] = [
        ModelRequest(
            parts=[
                SystemPromptPart("standing instruction"),
                SystemPromptPart("expired request instruction", scope="request"),
                UserPromptPart("old question"),
            ]
        ),
        ModelResponse(parts=[TextPart("old answer")]),
    ]
    payloads: list[dict[str, object]] = []

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelMessage]
    ) -> tuple[list[UserContent], list[ModelMessage]]:
        return ["projected prompt"], history if retain_history else []

    def add_current_system(ctx: RunContext[object], request: ModelRequestContext) -> ModelRequestContext:
        current = request.messages[-1]
        assert isinstance(current, ModelRequest)
        current.parts.append(SystemPromptPart("live request instruction", scope="request"))
        return request

    def handle(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return completion("offline", {"role": "assistant", "content": "done"}, streaming)

    async def on_text(text: str) -> None:
        pass

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(
            model, instructions="agent instruction", capabilities=[Hooks(before_model_request=add_current_system)]
        )
        options = AIRequestOptions(context_projection=project) if projection else None
        if streaming:
            result = await ai_run.run_ai_stream(
                agent, "current prompt", on_text, message_history=history, request_options=options
            )
        else:
            result = await ai_run.run_ai_text(agent, "current prompt", message_history=history, request_options=options)

    assert result.output == "done"
    assert len(payloads) == 1
    wire = json.dumps(payloads[0]["messages"])
    assert "expired request instruction" not in wire
    for expected in ("standing instruction", "agent instruction", "live request instruction"):
        assert wire.count(expected) == 1
    user_messages = [message for message in payloads[0]["messages"] if message["role"] == "user"]
    assert user_messages[-1]["content"] == (
        [{"type": "text", "text": "projected prompt"}] if projection else "current prompt"
    )
    assert ("old question" in wire) == (not projection or retain_history)
    assert ("old answer" in wire) == (not projection or retain_history)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("sequence_prompt", [False, True])
async def test_projection_replaces_only_current_prompt_after_hook_rewrite(
    streaming: bool, sequence_prompt: bool
) -> None:
    payloads: list[dict[str, object]] = []
    image = ImageUrl("https://example.com/context.png")

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelMessage]
    ) -> tuple[list[UserContent], list[ModelMessage]]:
        return ["projected prompt", image], []

    def rewrite_prompt(ctx: RunContext[object], request: ModelRequestContext) -> ModelRequestContext:
        current = request.messages[-1]
        assert isinstance(current, ModelRequest)
        current.parts = [
            SystemPromptPart("hook system instruction", scope="request"),
            UserPromptPart("rewritten prompt"),
            UserPromptPart(["extra context", image]),
        ]
        return request

    def handle(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return completion("offline", {"role": "assistant", "content": "done"}, streaming)

    async def on_text(text: str) -> None:
        pass

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(model, capabilities=[Hooks(before_model_request=rewrite_prompt)])
        prompt: str | list[UserContent] = ["original prompt"] if sequence_prompt else "original prompt"
        options = AIRequestOptions(context_projection=project)
        if streaming:
            result = await ai_run.run_ai_stream(agent, prompt, on_text, request_options=options)
        else:
            result = await ai_run.run_ai_text(agent, prompt, request_options=options)

    assert result.output == "done"
    assert len(payloads) == 1
    wire = json.dumps(payloads[0]["messages"])
    assert wire.count("projected prompt") == 1
    assert wire.count("extra context") == 1
    assert "original prompt" not in wire
    assert "rewritten prompt" not in wire
    assert wire.count("hook system instruction") == 1
    assert [message for message in payloads[0]["messages"] if message["role"] == "user"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "projected prompt"},
                {"type": "image_url", "image_url": {"url": image.url}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "extra context"},
                {"type": "image_url", "image_url": {"url": image.url}},
            ],
        },
    ]
