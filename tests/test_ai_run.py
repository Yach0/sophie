from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import BaseModel
from pydantic_ai import (
    Agent,
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage, UsageLimits

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import (
    AIModelCandidate,
    AIModelPlan,
    build_model_plan,
    request_has_images,
)
from sophie_bot.modules.ai.utils.ai_run import (
    AIRequestOptions,
    build_model_settings,
    run_ai_stream,
    run_ai_structured,
    run_ai_text,
)


@pytest.fixture(autouse=True)
def no_network_fallback(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.name == "test_the_last_resort_is_not_tried_twice_for_a_second_reasoning_effort":
        return
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


class StructuredOutput(BaseModel):
    value: str


@pytest.fixture
def no_stream_debounce(monkeypatch: Any) -> Iterator[None]:
    """Emit every accumulated update, so assertions see the full callback sequence."""
    monkeypatch.setattr(ai_run, "_STREAM_DEBOUNCE_SECONDS", 0.0)
    yield


def _part_round(part_cls: Any, delta_cls: Any, chunks: tuple[str, ...]) -> list[Any]:
    """Events for one model response that streams a single part of the given kind."""
    return [
        PartStartEvent(index=0, part=part_cls(content="")),
        *(PartDeltaEvent(index=0, delta=delta_cls(content_delta=chunk)) for chunk in chunks),
        PartEndEvent(index=0, part=part_cls(content="".join(chunks))),
    ]


def text_round(*chunks: str) -> list[Any]:
    return _part_round(TextPart, TextPartDelta, chunks)


def thinking_round(*chunks: str) -> list[Any]:
    return _part_round(ThinkingPart, ThinkingPartDelta, chunks)


def tool_call(tool_name: str) -> FunctionToolCallEvent:
    return FunctionToolCallEvent(part=ToolCallPart(tool_name=tool_name, args={}))


class FakeRunResult:
    usage = RunUsage(input_tokens=1, output_tokens=2, requests=1)

    def __init__(self, output: str) -> None:
        self.output = output

    def all_messages(self) -> list[ModelRequest | ModelResponse]:
        return [ModelResponse(parts=[TextPart(content=self.output)])]

    def new_messages(self) -> list[ModelRequest | ModelResponse]:
        return [ModelResponse(parts=[TextPart(content=self.output)])]


class FakeRunAgent:
    def __init__(self, model: TestModel | None = None) -> None:
        self.model = model or TestModel()
        self.run_kwargs: dict[str, Any] | None = None

    async def run(self, **kwargs: Any) -> FakeRunResult:
        self.run_kwargs = kwargs
        return FakeRunResult("hello")


class FakeEventAgent:
    """Replays scripted events through a Pydantic AI event stream handler."""

    def __init__(self, events: list[Any], final_output: str, raises: Exception | None = None) -> None:
        self.model = TestModel()
        self.events = events
        self.final_output = final_output
        self.raises = raises
        self.run_kwargs: dict[str, Any] | None = None

    async def run(self, **kwargs: Any) -> FakeRunResult:
        self.run_kwargs = kwargs
        event_stream_handler = kwargs["event_stream_handler"]

        async def stream() -> AsyncIterable[Any]:
            for event in self.events:
                yield event
            if self.raises is not None:
                raise self.raises

        await event_stream_handler(None, stream())
        return FakeRunResult(self.final_output)


class FakeStreamResult:
    usage = RunUsage(input_tokens=1, output_tokens=2, requests=1)

    @staticmethod
    async def stream_text(delta: bool, debounce_by: float) -> AsyncIterable[str]:
        assert delta is True
        assert debounce_by == 0.2
        for chunk in ("hel", "lo"):
            yield chunk

    @staticmethod
    async def get_output() -> str:
        return "hello"

    @staticmethod
    def all_messages() -> list[ModelRequest | ModelResponse]:
        return []

    @staticmethod
    def new_messages() -> list[ModelRequest | ModelResponse]:
        return []


class FakeStreamingAgent:
    def __init__(self) -> None:
        self.model = TestModel()

    @asynccontextmanager
    async def run_stream(self, **kwargs: Any) -> AsyncGenerator[FakeStreamResult]:
        event_handler = kwargs["event_stream_handler"]
        await event_handler(
            None,
            _tool_events("search", "search", "notes"),
        )
        yield FakeStreamResult()


async def _tool_events(*tool_names: str) -> AsyncIterable[FunctionToolCallEvent]:
    for tool_name in tool_names:
        yield tool_call(tool_name)


def test_build_model_settings_injects_openai_extra_body() -> None:
    model_settings = build_model_settings(
        {"temperature": 0, "extra_body": {"existing": "kept"}},
        AIRequestOptions(user_tracking_id="chat-iid", session_id="session-id", service_tier="flex"),
    )

    assert model_settings == {
        "temperature": 0,
        "extra_body": {
            "existing": "kept",
            "user": "chat-iid",
            "session_id": "session-id",
            "service_tier": "flex",
        },
    }


async def test_run_ai_text_wraps_output_usage_and_messages() -> None:
    agent = Agent(TestModel(custom_output_text="hello"), output_type=str)

    result = await run_ai_text(agent, "Say hello")

    assert result.output == "hello"
    assert result.usage.requests == 1
    assert result.message_history
    assert result.retries == 0


async def test_nonstreamed_chatbot_reports_each_tool_execution() -> None:
    agent = FakeEventAgent([tool_call("web_search"), tool_call("web_search")], "Done")
    actions: list[str] = []

    async def on_before_tool_call(tool_name: str) -> None:
        actions.append(tool_name)

    result = await run_ai_text(
        cast(Agent[Any, str], agent),
        "Search twice",
        on_before_tool_call=on_before_tool_call,
    )

    assert result.output == "Done"
    assert actions == ["web_search", "web_search"]


async def test_run_ai_structured_wraps_typed_output() -> None:
    agent = Agent(TestModel(custom_output_args={"value": "ok"}), output_type=StructuredOutput)

    result = await run_ai_structured(agent, "Return structured output")

    assert result.output == StructuredOutput(value="ok")
    assert result.usage.total_tokens


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("model_limit", [None, 0, 11])
async def test_native_requests_preserve_provider_generation_ceiling(streaming: bool, model_limit: int | None) -> None:
    observed: list[dict[str, object]] = []

    def request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        observed.append(dict(info.model_settings or {}))
        return ModelResponse(parts=[TextPart("bounded")])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncGenerator[str]:
        observed.append(dict(info.model_settings or {}))
        yield "bounded"

    model = FunctionModel(
        request, stream_function=stream, settings={"max_tokens": model_limit} if model_limit is not None else None
    )
    agent = Agent(model)
    if streaming:

        async def on_text(text: str) -> None:
            pass

        await run_ai_stream(agent, "hello", on_text, usage_limits=UsageLimits(output_tokens_limit=37))
    else:
        await run_ai_text(agent, "hello", usage_limits=UsageLimits(output_tokens_limit=37))
    assert observed == [{"max_tokens": model_limit if model_limit is not None else 37, "max_retries": 4}]


async def test_run_ai_stream_completes_tool_loop_and_uses_final_output(no_stream_debounce: None) -> None:
    streamed_text: list[str] = []
    tool_calls: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_tool_call(tool_name: str) -> None:
        tool_calls.append(tool_name)

    agent = FakeEventAgent(
        events=[
            *text_round("Let me check ", "the docs."),
            tool_call("sophie_help"),
            tool_call("sophie_help"),
            *text_round("Antiflood ", "works like this."),
        ],
        final_output="Antiflood works like this.",
    )

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        on_tool_call=on_tool_call,
    )

    assert result.output == "Antiflood works like this."
    assert tool_calls == ["sophie_help"]
    assert streamed_text[-1] == result.output
    assert "Let me check the docs." in streamed_text


async def test_run_ai_stream_does_not_duplicate_restarted_text_part(no_stream_debounce: None) -> None:
    streamed_text: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    formatted_answer = "Testing mode: *beep boop*"
    agent = FakeEventAgent(
        events=[
            *text_round(formatted_answer),
            *text_round("Testing mode: beep boop"),
        ],
        final_output=formatted_answer,
    )

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="Reply once",
        on_text_stream=on_text_stream,
    )

    assert result.output == formatted_answer
    assert streamed_text[-1] == formatted_answer


async def test_run_ai_stream_forces_latest_text_before_a_tool_call() -> None:
    callback_order: list[tuple[str, str]] = []

    async def on_text_stream(text: str) -> None:
        callback_order.append(("text", text))

    async def on_tool_call(tool_name: str) -> None:
        callback_order.append(("tool", tool_name))

    agent = FakeEventAgent(
        events=[*text_round("Let me check ", "the docs."), tool_call("sophie_help")],
        final_output="Let me check the docs.",
    )

    await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        on_tool_call=on_tool_call,
    )

    tool_index = callback_order.index(("tool", "sophie_help"))
    assert callback_order[tool_index - 1] == ("text", "Let me check the docs.")


@pytest.mark.asyncio
async def test_run_ai_stream_flushes_reasoning_before_a_tool_call() -> None:
    callback_order: list[tuple[str, str]] = []

    async def on_text_stream(_text: str) -> None:
        return None

    async def on_reasoning_stream(text: str) -> None:
        callback_order.append(("reasoning", text))

    async def on_before_tool_call(tool_name: str) -> None:
        callback_order.append(("tool", tool_name))

    agent = FakeEventAgent(
        events=[
            *thinking_round("First five words ", "and the rest of the reasoning."),
            tool_call("web_search"),
            tool_call("sophie_help"),
        ],
        final_output="Answer",
    )

    await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="Research this",
        on_text_stream=on_text_stream,
        on_reasoning_stream=on_reasoning_stream,
        on_before_tool_call=on_before_tool_call,
    )

    tool_index = callback_order.index(("tool", "web_search"))
    assert callback_order[tool_index - 1] == ("reasoning", "First five words and the rest of the reasoning.")
    assert callback_order.count(("reasoning", "First five words and the rest of the reasoning.")) == 1


async def test_run_ai_stream_routes_thinking_away_from_the_answer(no_stream_debounce: None) -> None:
    streamed_text: list[str] = []
    streamed_reasoning: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_reasoning_stream(text: str) -> None:
        streamed_reasoning.append(text)

    agent = FakeEventAgent(
        events=[*thinking_round("The user ", "wants antiflood."), *text_round("Antiflood works.")],
        final_output="Antiflood works.",
    )

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        on_reasoning_stream=on_reasoning_stream,
    )

    assert result.output == "Antiflood works."
    assert streamed_reasoning[-1] == "The user wants antiflood."
    assert all("wants antiflood" not in text for text in streamed_text)


async def test_run_ai_stream_propagates_usage_limit_after_partial_stream(no_stream_debounce: None) -> None:
    agent = FakeEventAgent(
        events=text_round("Partial answer"),
        final_output="",
        raises=UsageLimitExceeded("request limit exceeded"),
    )
    streamed_text: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    with pytest.raises(UsageLimitExceeded):
        await run_ai_stream(
            cast(Agent[Any, str], agent),
            user_prompt="How does antiflood work?",
            on_text_stream=on_text_stream,
        )

    assert streamed_text == ["Partial answer"]


class NamedTestModel(TestModel):
    """A ``TestModel`` that reports a name of its own.

    Every ``TestModel`` calls itself ``test``, and a chain identifies its models by name, so
    same-named stand-ins would look like one model to the last-resort dedup.
    """

    def __init__(self, model_name: str) -> None:
        super().__init__()
        self._name = model_name

    @property
    def model_name(self) -> str:
        return self._name


def candidate(
    model_name: str,
    *,
    supports_images: bool = True,
    service_tier: str | None = None,
) -> AIModelCandidate:
    return AIModelCandidate(
        model=NamedTestModel(model_name),
        model_name=model_name,
        supports_images=supports_images,
        service_tier=service_tier,
    )


async def run_chain(
    operation: Callable[[AIModelCandidate], Awaitable[str]],
    *candidates: AIModelCandidate,
) -> tuple[str, AIModelCandidate]:
    def request_for(active: AIModelCandidate) -> FunctionModel:
        async def request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(await operation(active))])

        return FunctionModel(request, model_name=active.model_name)

    native_candidates = tuple(replace(active, model=request_for(active)) for active in candidates)
    plan = AIModelPlan(native_candidates)
    result = await run_ai_text(Agent(plan.primary), "hello", model_plan=plan)
    served = next(
        original
        for original, native in zip(candidates, native_candidates, strict=True)
        if native.model is result.served_model
    )
    return result.output, served


def no_last_resort(monkeypatch: Any) -> None:
    """Drop the closing candidate, so a test asserts on exactly the chain it declared."""
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


async def test_the_first_candidate_serves_when_it_succeeds() -> None:
    primary = candidate("primary")
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        return "from-primary"

    result, served = await run_chain(operation, primary)

    assert result == "from-primary"
    assert served is primary
    assert attempted == [primary]


async def test_a_failing_candidate_hands_over_to_the_next() -> None:
    primary = candidate("primary")
    backup = candidate("backup")
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        if active is primary:
            raise TimeoutError("primary provider is down")
        return "from-backup"

    result, served = await run_chain(operation, primary, backup)

    # The served model must be the one that answered, so callers attribute usage/metrics correctly.
    assert result == "from-backup"
    assert served is backup
    assert attempted == [primary, backup]


async def test_a_flat_provider_rejection_also_hands_over() -> None:
    """The request that most needs another model — an image a model cannot read — is a plain 400."""
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        if active is primary:
            raise ModelHTTPError(status_code=400, model_name="primary", body="no image support")
        return "from-backup"

    result, served = await run_chain(operation, primary, backup)

    assert result == "from-backup"
    assert served is backup


async def test_a_usage_limit_stops_the_chain() -> None:
    """Sophie's own budget ended the run; the next model would only spend more of it."""
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        raise UsageLimitExceeded("request limit exceeded")

    with pytest.raises(UsageLimitExceeded):
        await run_chain(operation, candidate("primary"), candidate("backup"))
    assert len(attempted) == 1


@pytest.mark.parametrize("status_code", [401, 402, 403])
async def test_a_misconfigured_provider_stops_the_chain(status_code: int) -> None:
    """A rejected key fails identically on every candidate: walking the chain only delays the error."""
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        raise ModelHTTPError(status_code=status_code, model_name="primary", body="invalid api key")

    with pytest.raises(ModelHTTPError) as raised:
        await run_chain(operation, candidate("primary"), candidate("backup"), candidate("third"))

    assert raised.value.status_code == status_code
    assert len(attempted) == 1


async def test_the_last_candidates_error_is_raised() -> None:
    """Exhaustion keeps error semantics: the last candidate's failure is the one callers get."""

    async def operation(active: AIModelCandidate) -> str:
        raise TimeoutError("everything is down")

    with pytest.raises(AIRequestFailed) as raised:
        await run_chain(operation, candidate("primary"), candidate("backup"))

    assert isinstance(raised.value.__cause__, ExceptionGroup)
    assert str(raised.value.__cause__.exceptions[-1]) == "everything is down"


async def test_an_empty_answer_hands_over_to_the_next_candidate() -> None:
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        return "" if active is primary else "a real answer"

    result, served = await run_chain(operation, primary, backup)

    assert result == "a real answer"
    assert served is backup


async def test_the_last_candidates_empty_answer_is_returned() -> None:
    """An empty answer is still an answer; turning the end of the chain into an error would not be."""
    primary = candidate("primary")

    async def operation(active: AIModelCandidate) -> str:
        return " "

    result, served = await run_chain(operation, primary)

    assert result == " "
    assert served is primary


def test_the_agents_own_model_leads_and_the_last_resort_closes(monkeypatch: Any) -> None:
    agent_model = NamedTestModel("agent")
    last_resort = candidate("last-resort")
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: last_resort)
    plan = AIModelPlan(candidates=(candidate("plan"),))

    resolved = ai_run.resolve_candidates(agent_model, plan, has_images=False)

    assert [item.model_name for item in resolved] == ["agent", "plan", "last-resort"]


def test_the_agents_model_keeps_the_settings_of_the_plan_entry_it_came_from(monkeypatch: Any) -> None:
    """Otherwise the primary would lose the tier its own role declared the moment plans got involved."""
    no_last_resort(monkeypatch)
    primary = candidate("primary", service_tier="flex")
    plan = AIModelPlan(candidates=(primary,))

    resolved = ai_run.resolve_candidates(primary.model, plan, has_images=False)

    assert resolved == [primary]


def test_a_candidate_ruled_out_for_images_cannot_return_through_the_agent(monkeypatch: Any) -> None:
    no_last_resort(monkeypatch)
    text_only = candidate("text-only", supports_images=False)
    visual = candidate("visual")
    plan = AIModelPlan(candidates=(text_only, visual))

    # The agent was built with the plan's primary, which is exactly the model an image turn must skip.
    assert ai_run.resolve_candidates(text_only.model, plan, has_images=True) == [visual]
    assert ai_run.resolve_candidates(text_only.model, plan, has_images=False) == [text_only, visual]


def test_a_hand_picked_model_the_plan_never_had_still_leads(monkeypatch: Any) -> None:
    """Only a model the plan deliberately ruled out is dropped; a caller's own choice is not."""
    no_last_resort(monkeypatch)
    hand_picked = NamedTestModel("hand-picked")
    visual = candidate("visual")
    plan = AIModelPlan(candidates=(visual,))

    resolved = ai_run.resolve_candidates(hand_picked, plan, has_images=True)

    assert [item.model_name for item in resolved] == ["hand-picked", "visual"]


def test_a_long_chain_is_capped(monkeypatch: Any) -> None:
    """An operator may declare more candidates than one request should ever wait through."""
    last_resort = candidate("last-resort")
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: last_resort)
    plan = AIModelPlan(candidates=tuple(candidate(f"model-{index}") for index in range(6)))

    resolved = ai_run.resolve_candidates(plan.candidates[0].model, plan, has_images=False)

    assert len(resolved) == ai_run.AI_MAX_MODEL_ATTEMPTS
    # The cap never costs the safety net: the cheap model still closes the chain.
    assert resolved[-1] is last_resort


def test_the_last_resort_is_not_tried_twice_for_a_second_reasoning_effort(monkeypatch: Any) -> None:
    """A catalog entry for the last-resort model is the same upstream model, whatever effort it uses."""
    fallback_model = NamedTestModel("upstream/last-resort")
    monkeypatch.setattr(ai_run, "get_ai_model", lambda model_name: fallback_model)
    # Same catalog name, a different object because the role asked for another reasoning effort.
    role_entry = AIModelCandidate(
        model=NamedTestModel("upstream/last-resort"), model_name=ai_run.AI_FALLBACK_MODEL_NAME
    )

    assert ai_run._last_resort_candidate([role_entry]) is None
    assert ai_run._last_resort_candidate([candidate("something-else")]) is not None


def test_the_attempted_candidates_tier_is_the_one_sent_upstream() -> None:
    """Failover must not bill a second model at the first one's tier."""
    request_options = AIRequestOptions(session_id="s", service_tier="flex")

    inherited = ai_run._candidate_request_options(request_options, candidate("plain"))
    overridden = ai_run._candidate_request_options(request_options, candidate("priority", service_tier="priority"))
    opted_out = ai_run._candidate_request_options(request_options, candidate("free", service_tier="none"))

    assert inherited is request_options
    assert overridden is not None and overridden.service_tier == "priority"
    assert overridden.session_id == "s"
    assert opted_out is not None and opted_out.service_tier is None


def test_an_image_in_the_prompt_is_detected() -> None:
    png = BinaryContent(data=b"\x89PNG", media_type="image/png")

    assert request_has_images([png])
    assert request_has_images(["just text"]) is False
    assert request_has_images("just text") is False
    assert request_has_images(None) is False


def test_audio_is_not_an_image() -> None:
    """Audio reaches the model transcribed to text, which every candidate can read."""
    assert request_has_images([BinaryContent(data=b"RIFF", media_type="audio/wav")]) is False


def test_an_image_folded_into_the_history_is_detected() -> None:
    png = BinaryContent(data=b"\x89PNG", media_type="image/png")
    history = [ModelRequest(parts=[UserPromptPart(content=[png])])]

    assert request_has_images("follow-up question", history)


def test_a_plan_drops_a_model_that_already_appears_earlier() -> None:
    first = TestModel()
    duplicate = TestModel()
    plan = build_model_plan(
        [
            AIModelCandidate(model=first, model_name="same"),
            AIModelCandidate(model=duplicate, model_name="same"),
            AIModelCandidate(model=TestModel(), model_name="other"),
        ]
    )

    assert plan.model_names == ("same", "other")
    assert plan.primary is first


def test_an_empty_plan_cannot_serve_a_request() -> None:
    with pytest.raises(ValueError, match="no candidates"):
        _ = AIModelPlan().primary


def test_an_all_text_only_plan_still_tries_rather_than_refusing() -> None:
    """Better a model that may not see the image than no answer at all — the pre-filtering behaviour."""
    text_only = AIModelCandidate(model=TestModel(), model_name="text-only", supports_images=False)
    plan = AIModelPlan(candidates=(text_only,))

    assert plan.eligible(has_images=True) == (text_only,)
