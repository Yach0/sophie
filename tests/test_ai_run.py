from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import BaseModel
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
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
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, is_retryable_ai_provider_error
from sophie_bot.modules.ai.utils.ai_model_plan import (
    AIModelCandidate,
    AIModelPlan,
    build_model_plan,
    request_has_images,
)
from sophie_bot.modules.ai.utils.ai_refusal import AIModelRefused, is_refusal_output
from sophie_bot.modules.ai.utils.ai_run import (
    AIRequestOptions,
    ChatbotStreamOptions,
    build_model_settings,
    run_ai_stream,
    run_ai_structured,
    run_ai_text,
)


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


class FakeEventAgent:
    """Replays a scripted event stream through ``run_stream_events``."""

    def __init__(self, events: list[Any], final_output: str, raises: Exception | None = None) -> None:
        self.model = TestModel()
        self.events = events
        self.final_output = final_output
        self.raises = raises

    @asynccontextmanager
    async def run_stream_events(self, **_kwargs: Any) -> AsyncGenerator[AsyncIterable[Any]]:
        async def stream() -> AsyncIterable[Any]:
            for event in self.events:
                yield event
            if self.raises is not None:
                raise self.raises
            yield AgentRunResultEvent(cast(Any, FakeRunResult(self.final_output)))

        yield stream()


class FakeStreamResult:
    usage = RunUsage(input_tokens=1, output_tokens=2, requests=1)

    async def stream_text(self, delta: bool, debounce_by: float) -> AsyncIterable[str]:
        assert delta is True
        assert debounce_by == 0.2
        for chunk in ("hel", "lo"):
            yield chunk

    async def get_output(self) -> str:
        return "hello"

    def all_messages(self) -> list[ModelRequest | ModelResponse]:
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


async def test_run_ai_structured_wraps_typed_output() -> None:
    agent = Agent(TestModel(custom_output_args={"value": "ok"}), output_type=StructuredOutput)

    result = await run_ai_structured(agent, "Return structured output")

    assert result.output == StructuredOutput(value="ok")
    assert result.usage.total_tokens


async def test_run_ai_stream_legacy_path_sends_cumulative_text_and_deduplicates_tools() -> None:
    streamed_text: list[str] = []
    tool_calls: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_tool_call(tool_name: str) -> None:
        tool_calls.append(tool_name)

    result = await run_ai_stream(
        cast(Agent[Any, str], FakeStreamingAgent()),
        user_prompt="Say hello",
        on_text_stream=on_text_stream,
        on_tool_call=on_tool_call,
        stream_options=ChatbotStreamOptions(continuation=False),
    )

    assert streamed_text == ["hel", "hello"]
    assert tool_calls == ["search", "notes"]
    assert result.output == "hello"


async def test_run_ai_stream_concatenates_text_from_every_round(no_stream_debounce: None) -> None:
    """A model that narrates, calls a tool, then answers must keep both rounds of text.

    `Agent.run_stream` ended the run at the narration and dropped the answer; this is the
    regression guard for that.
    """
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

    assert result.output == "Let me check the docs.\n\nAntiflood works like this."
    assert tool_calls == ["sophie_help"]
    # Cumulative, never a bare delta, and the second round appends to the first.
    assert streamed_text[-1] == result.output
    assert streamed_text[1] == "Let me check "
    assert "Let me check the docs." in streamed_text


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


async def test_run_ai_stream_returns_partial_text_when_usage_limit_is_hit(no_stream_debounce: None) -> None:
    agent = FakeEventAgent(
        events=text_round("Partial answer"),
        final_output="",
        raises=UsageLimitExceeded("request limit exceeded"),
    )

    async def on_text_stream(_text: str) -> None:
        return None

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        stream_options=ChatbotStreamOptions(partial_on_limit=True),
    )

    assert result.truncated is True
    assert result.output == "Partial answer"


async def test_run_ai_stream_fails_on_usage_limit_without_partial_delivery(no_stream_debounce: None) -> None:
    agent = FakeEventAgent(
        events=text_round("Partial answer"),
        final_output="",
        raises=UsageLimitExceeded("request limit exceeded"),
    )

    async def on_text_stream(_text: str) -> None:
        return None

    with pytest.raises(AIRequestFailed):
        await run_ai_stream(
            cast(Agent[Any, str], agent),
            user_prompt="How does antiflood work?",
            on_text_stream=on_text_stream,
            stream_options=ChatbotStreamOptions(partial_on_limit=False),
        )


@pytest.fixture
def immediate_retries(monkeypatch: Any) -> Iterator[None]:
    """Run each attempt exactly once, so assertions see the candidate sequence and nothing else."""

    async def fake_retries(operation: Any, context: Any, on_retry: Any = None) -> Any:
        return await operation()

    monkeypatch.setattr(ai_run, "run_ai_request_with_retries", fake_retries)
    yield


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


def chain(*candidates: AIModelCandidate, failover: bool = True) -> ai_run.CandidateChain:
    """A chain with the rules the flag would give it, without going through plan resolution."""
    return ai_run.CandidateChain(
        candidates=list(candidates),
        should_try_next=ai_run._should_try_next_model if failover else is_retryable_ai_provider_error,
        refusal_failover=failover,
    )


def no_last_resort(monkeypatch: Any) -> None:
    """Drop the closing candidate, so a test asserts on exactly the chain it declared."""
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


async def test_the_first_candidate_serves_when_it_succeeds(immediate_retries: None) -> None:
    primary = candidate("primary")
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        return "from-primary"

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary))

    assert result == "from-primary"
    assert served is primary
    assert attempted == [primary]


async def test_a_failing_candidate_hands_over_to_the_next(immediate_retries: None) -> None:
    primary = candidate("primary")
    backup = candidate("backup")
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        if active is primary:
            raise TimeoutError("primary provider is down")
        return "from-backup"

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary, backup))

    # The served model must be the one that answered, so callers attribute usage/metrics correctly.
    assert result == "from-backup"
    assert served is backup
    assert attempted == [primary, backup]


async def test_a_flat_provider_rejection_also_hands_over(immediate_retries: None) -> None:
    """The request that most needs another model — an image a model cannot read — is a plain 400."""
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        if active is primary:
            raise ModelHTTPError(status_code=400, model_name="primary", body="no image support")
        return "from-backup"

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary, backup))

    assert result == "from-backup"
    assert served is backup


async def test_a_usage_limit_stops_the_chain(immediate_retries: None) -> None:
    """Sophie's own budget ended the run; the next model would only spend more of it."""
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        raise UsageLimitExceeded("request limit exceeded")

    with pytest.raises(AIRequestFailed) as raised:
        await ai_run._run_with_model_candidates(operation, chain(candidate("primary"), candidate("backup")))

    assert isinstance(raised.value.__cause__, UsageLimitExceeded)
    assert len(attempted) == 1


@pytest.mark.parametrize("status_code", [401, 402, 403])
async def test_a_misconfigured_provider_stops_the_chain(immediate_retries: None, status_code: int) -> None:
    """A rejected key fails identically on every candidate: walking the chain only delays the error."""
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        raise ModelHTTPError(status_code=status_code, model_name="primary", body="invalid api key")

    with pytest.raises(AIRequestFailed) as raised:
        await ai_run._run_with_model_candidates(
            operation, chain(candidate("primary"), candidate("backup"), candidate("third"))
        )

    assert isinstance(raised.value.__cause__, ModelHTTPError)
    assert len(attempted) == 1


async def test_the_last_candidates_error_is_raised(immediate_retries: None) -> None:
    """Exhaustion keeps error semantics: the last candidate's failure is the one callers get."""

    async def operation(active: AIModelCandidate) -> str:
        raise TimeoutError("everything is down")

    with pytest.raises(AIRequestFailed) as raised:
        await ai_run._run_with_model_candidates(operation, chain(candidate("primary"), candidate("backup")))

    assert str(raised.value.__cause__) == "everything is down"


async def test_an_empty_answer_hands_over_to_the_next_candidate(immediate_retries: None) -> None:
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        return "" if active is primary else "a real answer"

    result, served = await ai_run._run_with_model_candidates(
        operation, chain(primary, backup), is_refusal=is_refusal_output
    )

    assert result == "a real answer"
    assert served is backup


async def test_the_last_candidates_empty_answer_is_returned(immediate_retries: None) -> None:
    """An empty answer is still an answer; turning the end of the chain into an error would not be."""
    primary = candidate("primary")

    async def operation(active: AIModelCandidate) -> str:
        return ""

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary), is_refusal=is_refusal_output)

    assert result == ""
    assert served is primary


async def test_a_raised_refusal_hands_over_to_the_next_candidate(immediate_retries: None) -> None:
    """An output validator opting into failover is caught, exactly as the class promises."""
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        if active is primary:
            raise AIModelRefused("primary")
        return "a real answer"

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary, backup))

    assert result == "a real answer"
    assert served is backup


async def test_the_last_candidates_raised_refusal_reaches_the_caller(immediate_retries: None) -> None:
    """Unlike an empty output, a raised refusal carries no answer to hand back."""

    async def operation(active: AIModelCandidate) -> str:
        raise AIModelRefused(active.model_name)

    with pytest.raises(AIModelRefused):
        await ai_run._run_with_model_candidates(operation, chain(candidate("primary")))


async def test_with_failover_off_only_a_retryable_error_moves_a_request(immediate_retries: None) -> None:
    """The pre-plan rule: a flat rejection fails fast instead of walking the chain."""
    primary = candidate("primary")
    backup = candidate("backup")
    attempted: list[AIModelCandidate] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active)
        raise ModelHTTPError(status_code=400, model_name="primary", body="malformed request")

    with pytest.raises(AIRequestFailed) as raised:
        await ai_run._run_with_model_candidates(operation, chain(primary, backup, failover=False))

    assert isinstance(raised.value.__cause__, ModelHTTPError)
    assert attempted == [primary]


async def test_with_failover_off_a_retryable_error_still_reaches_the_last_resort(immediate_retries: None) -> None:
    primary = candidate("primary")
    last_resort = candidate("last-resort")

    async def operation(active: AIModelCandidate) -> str:
        if active is primary:
            raise TimeoutError("primary provider is down")
        return "from-last-resort"

    result, served = await ai_run._run_with_model_candidates(operation, chain(primary, last_resort, failover=False))

    assert result == "from-last-resort"
    assert served is last_resort


async def test_with_failover_off_an_empty_answer_is_kept(immediate_retries: None) -> None:
    """Refusal failover is part of the new behaviour, so the flag has to hold it back too."""
    primary = candidate("primary")
    backup = candidate("backup")

    async def operation(active: AIModelCandidate) -> str:
        return "" if active is primary else "a real answer"

    result, served = await ai_run._run_with_model_candidates(
        operation, chain(primary, backup, failover=False), is_refusal=is_refusal_output
    )

    assert result == ""
    assert served is primary


def test_with_failover_off_only_the_agents_model_and_the_last_resort_are_tried(monkeypatch: Any) -> None:
    agent_model = NamedTestModel("agent")
    last_resort = candidate("last-resort")
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: last_resort)
    plan = AIModelPlan(
        candidates=(
            AIModelCandidate(model=agent_model, model_name="agent"),
            candidate("backup"),
        )
    )

    resolved = ai_run.build_candidate_chain(agent_model, plan, has_images=False)

    assert [item.model_name for item in resolved.candidates] == ["agent", "last-resort"]
    assert resolved.refusal_failover is False
    assert resolved.should_try_next is is_retryable_ai_provider_error


def test_a_plan_without_the_flag_never_walks_its_chain(monkeypatch: Any) -> None:
    """The plan still lists every candidate — the panel shows them — but only the first one runs."""
    no_last_resort(monkeypatch)
    agent_model = NamedTestModel("agent")
    plan = AIModelPlan(candidates=(AIModelCandidate(model=agent_model, model_name="agent"), candidate("backup")))

    assert len(ai_run.resolve_candidates(agent_model, plan, has_images=False)) == 1
    assert len(ai_run.resolve_candidates(agent_model, replace(plan, failover=True), has_images=False)) == 2


def test_the_agents_own_model_leads_and_the_last_resort_closes(monkeypatch: Any) -> None:
    agent_model = NamedTestModel("agent")
    last_resort = candidate("last-resort")
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: last_resort)
    plan = AIModelPlan(candidates=(candidate("plan"),), failover=True)

    resolved = ai_run.resolve_candidates(agent_model, plan, has_images=False)

    assert [item.model_name for item in resolved] == ["agent", "plan", "last-resort"]


def test_the_agents_model_keeps_the_settings_of_the_plan_entry_it_came_from(monkeypatch: Any) -> None:
    """Otherwise the primary would lose the tier its own role declared the moment plans got involved."""
    no_last_resort(monkeypatch)
    primary = candidate("primary", service_tier="flex")
    plan = AIModelPlan(candidates=(primary,), failover=True)

    resolved = ai_run.resolve_candidates(primary.model, plan, has_images=False)

    assert resolved == [primary]


def test_a_candidate_ruled_out_for_images_cannot_return_through_the_agent(monkeypatch: Any) -> None:
    no_last_resort(monkeypatch)
    text_only = candidate("text-only", supports_images=False)
    visual = candidate("visual")
    plan = AIModelPlan(candidates=(text_only, visual), failover=True)

    # The agent was built with the plan's primary, which is exactly the model an image turn must skip.
    assert ai_run.resolve_candidates(text_only.model, plan, has_images=True) == [visual]
    assert ai_run.resolve_candidates(text_only.model, plan, has_images=False) == [text_only, visual]


def test_a_hand_picked_model_the_plan_never_had_still_leads(monkeypatch: Any) -> None:
    """Only a model the plan deliberately ruled out is dropped; a caller's own choice is not."""
    no_last_resort(monkeypatch)
    hand_picked = NamedTestModel("hand-picked")
    visual = candidate("visual")
    plan = AIModelPlan(candidates=(visual,), failover=True)

    resolved = ai_run.resolve_candidates(hand_picked, plan, has_images=True)

    assert [item.model_name for item in resolved] == ["hand-picked", "visual"]


def test_a_long_chain_is_capped(monkeypatch: Any) -> None:
    """An operator may declare more candidates than one request should ever wait through."""
    last_resort = candidate("last-resort")
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: last_resort)
    plan = AIModelPlan(candidates=tuple(candidate(f"model-{index}") for index in range(6)), failover=True)

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
