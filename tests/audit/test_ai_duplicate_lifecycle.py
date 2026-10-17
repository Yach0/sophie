"""Offline regression through real dispatcher, middleware, cache and provider lifecycles."""

from __future__ import annotations

import asyncio
import json
import socket
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import TypedDict, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    Chat,
    ErrorEvent,
    Message,
    MessageEntity,
    RichBlockParagraph,
    RichMessage,
    TelegramObject,
    Update,
    User,
)
from aiogram_test_framework.mock_bot import MockBot, MockSession
from aiogram_test_framework.request_capture import RequestCapture
from fakeredis import FakeAsyncRedis
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from redis.asyncio.client import Pipeline

from sophie_bot.config import CONFIG
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.db.models import ChatModel, FiltersModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.middlewares import enable_middlewares
from sophie_bot.modules import assemble_bot_modules, discover_modules, initialize_modules
from sophie_bot.modules.ai.utils import ai_model_pricing
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_quota import set_monthly_quota
from sophie_bot.modules.ai.utils.cache_messages import get_message_cache_key
from sophie_bot.modules.ai.utils.proactive_replies import ProactiveDecision
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.db import DatabaseResources
from sophie_bot.shared.message_text import message_text
from sophie_bot.utils.cached import RedisCache
from sophie_bot.utils.feature_flags import set_value
from sophie_bot.utils.i18n import I18nNew
from tests.utils import mongo_mock
from tests.utils.db_fixture import MOCK_MONGO, cleanup_beanie

SENTINEL = "AUDIT_CURRENT_SENTINEL"
CHAT_TID = -1009876543210
USER_TID = 876543210


class SerializedPart(TypedDict):
    part_kind: str
    content: str | list[object]


class SerializedMessage(TypedDict):
    parts: list[SerializedPart]


class CachedMessage(TypedDict):
    message_id: int
    user_id: int
    username: str | None


class Identity(TypedDict):
    chat_tid: int
    message_id: int
    user_tid: int
    cache_read_records: int
    rendered_candidates: list[str]
    occurrences: int
    locations: list[dict[str, int | str]]


class ProviderCall(TypedDict):
    purpose: str
    instructions: object
    instruction_fragments: list[str]
    messages: list[SerializedMessage]
    system_prompts: list[str | list[object]]
    output_tools: list[dict[str, object]]
    telegram_identities: list[Identity]
    sentinel_occurrences: int


class TimelineEntry(TypedDict, total=False):
    sequence: int
    kind: str
    message: CachedMessage
    messages: list[CachedMessage]
    message_id: int
    purpose: str


@dataclass
class AuditRuntime:
    dispatcher: Dispatcher
    services: ApplicationServices
    telegram_session: AuditTelegramSession
    timeline: list[TimelineEntry] = field(default_factory=list)
    calls: list[ProviderCall] = field(default_factory=list)
    current_message_id: int = 0
    inputs: list[Message] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    capture_requests: RequestCapture = field(default_factory=RequestCapture)

    def record(self, kind: str, **details: object) -> None:
        self.timeline.append(cast(TimelineEntry, {"sequence": len(self.timeline), "kind": kind, **details}))

    async def capture(self, messages: list[ModelMessage], info: AgentInfo, purpose: str) -> None:
        serialized = cast(list[SerializedMessage], json.loads(ModelMessagesTypeAdapter.dump_json(messages)))
        fragments = provider_text_fragments(serialized)
        # Provider history drops Telegram IDs. Correlate exact rendered lines with the
        # observed cache metadata and fed Telegram objects; never infer identity from sentinel.
        identities: list[Identity] = []
        for source in self.inputs:
            assert source.from_user is not None
            cached = [
                stored
                for entry in self.timeline
                if entry["kind"] == "cache_read"
                for stored in entry["messages"]
                if stored["message_id"] == source.message_id and stored["user_id"] == source.from_user.id
            ]
            names = {source.from_user.full_name}
            for stored in cached:
                user = await ChatModel.get_by_tid(stored["user_id"])
                names.add(user.first_name_or_title if user else "Unknown")
                names.add(stored["username"] or str(stored["user_id"]))
            source_text = source.text or ""
            rendered_text = source_text.removeprefix("/ai ") if purpose == "command" else source_text
            expected_lines = {f"{name}: {rendered_text}" for name in names}
            locations: list[dict[str, int | str]] = []
            for fragment_index, fragment in enumerate(fragments):
                for line_index, line in enumerate(fragment.splitlines()):
                    plain_line = line.removeprefix("- ")
                    decision_row = (
                        purpose == "decision"
                        and line.startswith(f"message_id={source.message_id} | ")
                        and line.endswith(f" | text={source_text}")
                        and any(f" | user={name} | " in line for name in names)
                    )
                    matching_row = purpose == "matching" and line == f"Message content: {source_text}"
                    if plain_line in expected_lines or decision_row or matching_row:
                        locations.append({"fragment": fragment_index, "line": line_index, "text": line})
            identities.append(
                {
                    "chat_tid": source.chat.id,
                    "message_id": source.message_id,
                    "user_tid": source.from_user.id,
                    "cache_read_records": len(cached),
                    "rendered_candidates": sorted(expected_lines),
                    "occurrences": len(locations),
                    "locations": locations,
                }
            )
        self.record("provider", purpose=purpose, call_index=len(self.calls))
        self.calls.append(
            {
                "purpose": purpose,
                "instructions": info.instructions,
                "instruction_fragments": instruction_fragments(info.instructions),
                "messages": serialized,
                "system_prompts": [
                    part["content"]
                    for message in serialized
                    for part in message["parts"]
                    if part["part_kind"] == "system-prompt"
                ],
                "output_tools": [
                    {"name": tool.name, "schema": tool.parameters_json_schema} for tool in info.output_tools
                ],
                "telegram_identities": identities,
                "sentinel_occurrences": sum(fragment.count(SENTINEL) for fragment in fragments),
            }
        )

    async def feed(self, message_id: int, text: str, *, command: bool = False) -> None:
        self.current_message_id = message_id
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC) - timedelta(seconds=1),
            chat=Chat(id=CHAT_TID, type="supergroup", title="Offline audit group"),
            from_user=User(id=USER_TID, is_bot=False, first_name="AuditUser", username="audit_user"),
            text=text,
            entities=[MessageEntity(type="bot_command", offset=0, length=3)] if command else None,
        )
        self.inputs.append(message)
        update = Update(update_id=10000 + message_id, message=message)
        self.record("feed_update", update_id=update.update_id, chat_tid=CHAT_TID, message_id=message_id, text=text)
        await asyncio.wait_for(self.dispatcher.feed_update(self.services.bot, update), timeout=20)
        self.record("feed_return", message_id=message_id)


def provider_text_fragments(messages: list[SerializedMessage]) -> list[str]:
    """Pydantic AI serializes user content as either a string or a list of parts."""
    fragments: list[str] = []
    for message in messages:
        for part in message["parts"]:
            if part["part_kind"] != "user-prompt":
                continue
            content = part["content"]
            if isinstance(content, str):
                fragments.append(content)
            else:
                fragments.extend(item for item in content if isinstance(item, str))
    return fragments


def instruction_fragments(instructions: object) -> list[str]:
    """Capture resolved provider instructions without iterating strings as characters.

    Dynamic agent instructions are evaluated by Pydantic AI before FunctionModel
    receives AgentInfo. Never evaluate agent callbacks or invent absent prompts here.
    """
    if instructions is None:
        return []
    if isinstance(instructions, str):
        return [instructions]
    if isinstance(instructions, (list, tuple)) and all(isinstance(item, str) for item in instructions):
        return list(instructions)
    raise TypeError(f"Unresolved provider instructions: {type(instructions).__name__}")


class VisibleHTML(HTMLParser):
    """Project this audit's text-only Telegram HTML to a received rich paragraph."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []

    def handle_data(self, data: str) -> None:
        self.fragments.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "h1"}:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "h1"}:
            self.fragments.append("\n")


class AuditTelegramSession(MockSession):
    """Repair Telegram boundary contracts while retaining RequestCapture and bot dispatch."""

    def __init__(self, capture: RequestCapture, bot_user: User) -> None:
        super().__init__(capture, bot_user)
        self.api_methods: list[str] = []
        self.messages: dict[tuple[int, int], Message] = {}

    def _generate_response(self, bot: Bot, method_name: str, params: dict[str, object]) -> object:
        self.api_methods.append(method_name)
        if method_name in {"sendMessage", "sendRichMessage", "editMessageText", "editMessageReplyMarkup"}:
            chat_tid = int(cast(int | str, params["chat_id"]))
            message_id = (
                int(cast(int | str, params["message_id"])) if "message_id" in params else self._get_next_message_id()
            )
            previous = self.messages.get((chat_tid, message_id))
            text = params.get("text", previous.text if previous else None)
            rich_message = previous.rich_message if previous else None
            if "rich_message" in params:
                parser = VisibleHTML()
                parser.feed(cast(dict[str, str], params["rich_message"])["html"])
                rich_message = RichMessage(blocks=[RichBlockParagraph(text="".join(parser.fragments).strip())])
            response = Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_tid, type="supergroup", title="Offline audit group"),
                from_user=self._bot_user,
                text=cast(str | None, text),
                rich_message=rich_message,
                message_thread_id=cast(int | None, params.get("message_thread_id")),
            ).as_(bot)
            self.messages[(chat_tid, message_id)] = response
            return response
        response = super()._generate_response(bot, method_name, params)
        return response.as_(bot) if isinstance(response, Message) else response


class ImmediateMongoExecutor:
    """Keep the in-memory Mongo adapter synchronous; no thread scheduling or real DB I/O."""

    async def run_in_executor(self, _executor: object, function: Callable[..., object], *args: object) -> object:
        return function(*args)


@pytest.fixture(scope="session", autouse=True)
def offline_mongo_executor() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        executor = ImmediateMongoExecutor()
        patch.setattr(mongo_mock, "asyncio", SimpleNamespace(get_event_loop=lambda: executor))
        yield


def deny_network(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Offline audit attempted a real socket connection")


@pytest_asyncio.fixture
async def audit_runtime(
    db_init: AsyncDatabase, i18n_context: I18nNew, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AuditRuntime]:
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    # CONFIG may come from a developer environment. No external telemetry/proxy work is allowed.
    monkeypatch.setattr(CONFIG, "sentry_url", None)
    monkeypatch.setattr(CONFIG, "proxy_enable", False)
    monkeypatch.setattr(CONFIG, "debug_mode", "off")
    monkeypatch.setattr(CONFIG, "memory_debug", False)

    async def immediate_callback(callback: Callable[[], object]) -> object:
        return callback()

    # Execute aiogram's sync filter callbacks unchanged, without worker scheduling.
    # The sandbox cannot reliably complete to_thread futures. Async dispatch stays real.
    monkeypatch.setattr("aiogram.dispatcher.event.handler.asyncio", SimpleNamespace(to_thread=immediate_callback))

    async def offline_classifier(function: Callable[[str], object], text: str) -> object:
        return function(text)

    def classifier_result(text: str) -> dict[str, float]:
        return {"spam_probability": 0.0, "nsfw_probability": 0.0}

    # USSR's unrelated ML artifact is absent. Retain its middleware and replace only
    # the classifier boundary, avoiding worker threads for this deterministic fixture.
    monkeypatch.setattr("sophie_bot.middlewares.spam_detection.predict_message", classifier_result)
    monkeypatch.setattr("sophie_bot.middlewares.spam_detection.asyncio", SimpleNamespace(to_thread=offline_classifier))
    redis = FakeAsyncRedis(decode_responses=False, single_connection_client=True)
    assert await redis.dbsize() == 0
    capture = RequestCapture()
    bot = MockBot(
        capture=capture, token=CONFIG.token, bot_id=CONFIG.bot_id, bot_username="test_bot", bot_first_name="Sophie"
    )
    session = AuditTelegramSession(capture, bot.bot_user)
    session._message_id_counter = 1000
    bot.session = session
    bot.get_chat_administrators = AsyncMock(return_value=[])
    cache = RedisCache(redis)
    services = ApplicationServices(
        bot=bot,
        redis=redis,
        db=DatabaseResources(mongo=cast(AsyncMongoClient, MOCK_MONGO), database=db_init, initialized=True),
        cache=cache,
        locales=LocaleStore(cache, i18n_context, CONFIG.default_locale),
        deletions=DelayedDeletionService(bot),
        modules=discover_modules(("*",)),
        background_tasks=set(),
    )
    storage = RedisStorage(redis=redis, key_builder=DefaultKeyBuilder(prefix=str(CONFIG.redis_db_fsm)))
    dispatcher = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    dispatcher.workflow_data["services"] = services
    runtime = AuditRuntime(dispatcher=dispatcher, services=services, telegram_session=session, capture_requests=capture)
    original_response = session._generate_response

    def observed_response(bot: Bot, method_name: str, params: dict[str, object]) -> object:
        response = original_response(bot, method_name, params)
        runtime.record(
            "telegram_response",
            api_method=method_name,
            response_type=type(response).__name__,
            message_id=response.message_id if isinstance(response, Message) else None,
        )
        return response

    monkeypatch.setattr(session, "_generate_response", observed_response)

    async def observe_error(
        handler: Callable[[TelegramObject, dict[str, object]], Awaitable[object]],
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        assert isinstance(event, ErrorEvent)
        error = {
            "type": type(event.exception).__name__,
            "message": str(event.exception),
            "traceback": "".join(traceback.format_exception(event.exception)),
        }
        runtime.errors.append(error)
        runtime.record("error", **error)
        return await handler(event, data)

    dispatcher.errors.outer_middleware(observe_error)

    original_pipeline = redis.pipeline
    original_read = redis.zrangebyscore

    def observed_pipeline(transaction: bool = True, shard_hint: str | None = None) -> Pipeline:
        pipeline = original_pipeline(transaction=transaction, shard_hint=shard_hint)
        original_execute = pipeline.execute

        async def observed_execute(raise_on_error: bool = True) -> object:
            writes = [
                {"key": str(command[1]), "message": json.loads(command[3])}
                for command, _options in pipeline.command_stack
                if str(command[0]).upper() == "ZADD" and str(command[1]).startswith("messages:")
            ]
            result = await original_execute(raise_on_error=raise_on_error)
            for write in writes:
                runtime.record("cache_write", **write)
            return result

        monkeypatch.setattr(pipeline, "execute", observed_execute)
        return pipeline

    async def observed_read(
        name: str,
        min: float | str,
        max: float | str,
        start: int | None = None,
        num: int | None = None,
        withscores: bool = False,
        score_cast_func: Callable[[str], float] = float,
    ) -> object:
        result = await cast(
            Awaitable[list[bytes]],
            original_read(name, min, max, start=start, num=num, withscores=withscores, score_cast_func=score_cast_func),
        )
        if name.startswith("messages:"):
            runtime.record("cache_read", key=name, messages=[json.loads(raw) for raw in result])
        return result

    monkeypatch.setattr(redis, "pipeline", observed_pipeline)
    monkeypatch.setattr(redis, "zrangebyscore", observed_read)
    # Pricing is a paid-provider HTTP boundary; retain real quota checks and charging.
    monkeypatch.setattr(ai_model_pricing, "get_model_pricing", AsyncMock(return_value=(None, None)))

    def model_plan(purpose: str) -> AIModelPlan:
        async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await runtime.capture(messages, info, purpose)
            if purpose == "matching":
                output = {"matches": True, "reasoning": "offline provider fixture"}
            elif purpose == "decision":
                # Choose only an identity actually presented to the provider, not a preseeded target.
                prompt = "\n".join(provider_text_fragments(runtime.calls[-1]["messages"]))
                candidate_ids = [
                    int(line.split(" | ")[0].removeprefix("message_id="))
                    for line in prompt.splitlines()
                    if line.startswith("message_id=")
                ]
                assert candidate_ids, "Decision fixture must select a real provider-visible candidate"
                output = ProactiveDecision.model_validate(
                    {"actions": [{"action": "answer", "message_id": candidate_ids[-1]}]}
                ).model_dump(mode="json")
                runtime.record("decision_fixture", candidate_ids=candidate_ids, output=output)
            else:
                return ModelResponse(parts=[TextPart(content="Offline audit answer.")])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(output))])

        async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            await runtime.capture(messages, info, purpose)
            yield "Offline audit answer."

        model = FunctionModel(response, stream_function=stream, model_name=f"offline-audit-{purpose}")
        return AIModelPlan((AIModelCandidate(model, model.model_name),))

    for target, purpose in (
        ("sophie_bot.modules.ai.magic_handlers.modern_action.get_chat_default_model_plan", "filter_reply"),
        ("sophie_bot.modules.filters.utils_.match_handler.get_chat_filters_model_plan", "matching"),
        ("sophie_bot.modules.ai.utils.ai_chatbot_reply.get_chat_default_model_plan", "command"),
        ("sophie_bot.modules.ai.utils.proactive_replies.get_proactive_replies_model_plan", "decision"),
        ("sophie_bot.modules.ai.utils.proactive_replies.get_chat_default_model_plan", "proactive_answer"),
    ):
        monkeypatch.setattr(target, AsyncMock(return_value=model_plan(purpose)))
    for target in (
        "sophie_bot.modules.ai.utils.ai_chatbot_reply.resolve_chat_service_tier",
        "sophie_bot.modules.ai.utils.proactive_replies.resolve_chat_service_tier",
        "sophie_bot.modules.filters.utils_.match_handler.resolve_chat_service_tier",
    ):
        monkeypatch.setattr(target, AsyncMock(return_value=None))

    await asyncio.wait_for(initialize_modules(services), timeout=20)
    await assemble_bot_modules(dispatcher, services)
    enable_middlewares(dispatcher, services)
    runtime.record(
        "registration",
        fsm_storage=type(dispatcher.storage).__name__,
        fsm_key_prefix=cast(DefaultKeyBuilder, storage.key_builder).prefix,
        fsm_event_isolation=type(dispatcher.fsm.events_isolation).__name__,
        fsm_uses_services_redis=storage.redis is services.redis,
        update_outer=[type(middleware).__name__ for middleware in dispatcher.update.outer_middleware],
        update_inner=[type(middleware).__name__ for middleware in dispatcher.update.middleware],
        dispatcher_message_inner=[type(middleware).__name__ for middleware in dispatcher.message.middleware],
        routers=[router.name for router in dispatcher.sub_routers],
        message_outer={
            router.name: [type(middleware).__name__ for middleware in router.message.outer_middleware]
            for router in dispatcher.sub_routers
        },
        message_inner={
            router.name: [
                getattr(middleware, "__qualname__", type(middleware).__name__)
                for middleware in router.message.middleware
            ]
            for router in dispatcher.sub_routers
        },
    )
    try:
        # Registration through the real SaveChatsMiddleware while AI is unconfigured (disabled).
        await runtime.feed(1, "Register this offline group")
        chat = await ChatModel.get_by_tid(CHAT_TID)
        assert chat is not None
        await AIModeModel.set_mode(chat, AIMode.support)
        await set_monthly_quota(chat, 100000)
        await set_value("ai_moderation", False, redis=redis)
        await set_value("ai_filters_jev", False, redis=redis)
        assert await redis.zcard(get_message_cache_key(CHAT_TID)) == 0
        runtime.record("initial_message_cache_empty")
        yield runtime
    finally:
        await dispatcher.fsm.events_isolation.close()
        await dispatcher.storage.close()
        await services.close()
        await cleanup_beanie()


async def install_filter(runtime: AuditRuntime, handler: str) -> None:
    chat = await ChatModel.get_by_tid(CHAT_TID)
    assert chat is not None
    await FiltersModel(
        chat=chat,
        handler=handler,
        version=2,
        action=None,
        actions={"ai_text": {"prompt": "AUDIT_SYSTEM_PROMPT: Reply briefly."}},
    ).save()


def assert_telegram_boundary(runtime: AuditRuntime) -> None:
    assert runtime.errors == [], f"Harness/dispatch errors: {runtime.errors}"
    methods = runtime.telegram_session.api_methods
    requests = runtime.capture_requests.all_requests
    assert len(methods) == len(requests)
    responses: list[Message] = []
    for method, request in zip(methods, requests, strict=True):
        if method in {"sendMessage", "sendRichMessage", "editMessageText", "editMessageReplyMarkup"}:
            response = request.response
            assert isinstance(response, Message), method
            assert response.bot is runtime.services.bot
            assert response.chat.id == CHAT_TID
            if "message_id" in request.params:
                assert response.message_id == request.params["message_id"]
            responses.append(response)
    assert any("Offline audit answer." in message_text(response) for response in responses)


def assert_provider_contract(runtime: AuditRuntime, purpose: str) -> ProviderCall:
    call = next(call for call in runtime.calls if call["purpose"] == purpose)
    assert runtime.errors == [], f"Harness/dispatch errors: {runtime.errors}"
    systems = call["system_prompts"]
    if purpose == "filter_reply":
        assert systems == ["AUDIT_SYSTEM_PROMPT: Reply briefly."]
        assert call["instruction_fragments"] == []
    elif purpose == "matching":
        assert systems == [
            (
                "You are a content moderation assistant. Evaluate whether the provided message content "
                "matches the filter criteria. Be precise and objective in your assessment."
            )
        ]
        assert call["instruction_fragments"] == []
    elif purpose == "decision":
        assert systems == [
            (
                "Return structured JSON only. Sophie is usually silent and only joins when her contribution "
                "is clearly timely, useful, or funny. Prefer none unless there is a strong natural opening; "
                "use reactions for lightweight moments."
            )
        ]
        assert call["instruction_fragments"] == []
    else:
        assert any("You're a telegram bot named Sophie." in chunk for chunk in call["instruction_fragments"])
        if purpose == "proactive_answer":
            assert any("You are proactively joining a Telegram group chat." in prompt for prompt in systems)
        else:
            assert systems == []
    return call


def assert_no_duplicate(runtime: AuditRuntime, purpose: str, *, cache_read_records: int) -> None:
    call = assert_provider_contract(runtime, purpose)
    identity = next(
        identity for identity in call["telegram_identities"] if identity["message_id"] == runtime.current_message_id
    )
    assert identity["chat_tid"] == CHAT_TID and identity["user_tid"] == USER_TID
    assert identity["cache_read_records"] == cache_read_records
    assert identity["occurrences"] == 1, (
        f"Expected one Telegram identity ({CHAT_TID}, {runtime.current_message_id}), "
        f"captured {identity['occurrences']}; independent sentinel count={call['sentinel_occurrences']}"
    )
    assert call["sentinel_occurrences"] == 1, (
        f"Expected no duplicate current input, captured {call['sentinel_occurrences']} sentinel occurrences; "
        "see the captured provider request and cache timeline"
    )


async def assert_source_and_cache_counts(runtime: AuditRuntime, *, read_counts: list[int]) -> None:
    """The current identity is persisted once, with scenario-specific read visibility."""
    current_id = runtime.current_message_id
    assert sum(message.message_id == current_id for message in runtime.inputs) == 1
    feeds = [entry for entry in runtime.timeline if entry["kind"] == "feed_update"]
    writes = [entry for entry in runtime.timeline if entry["kind"] == "cache_write"]
    reads = [entry for entry in runtime.timeline if entry["kind"] == "cache_read"]
    assert sum(entry["message_id"] == current_id for entry in feeds) == 1
    assert sum(entry["message"]["message_id"] == current_id for entry in writes) == 1
    assert [sum(message["message_id"] == current_id for message in entry["messages"]) for entry in reads] == read_counts
    stored = [json.loads(raw) for raw in await runtime.services.redis.zrange(get_message_cache_key(CHAT_TID), 0, -1)]
    assert sum(message["message_id"] == current_id and message["user_id"] == USER_TID for message in stored) == 1


def assert_current_cached_after_providers(runtime: AuditRuntime) -> None:
    current_writes = [
        entry
        for entry in runtime.timeline
        if entry["kind"] == "cache_write"
        and entry["message"]["message_id"] == runtime.current_message_id
        and entry["message"]["user_id"] == USER_TID
    ]
    assert len(current_writes) == 1
    provider_sequences = [entry["sequence"] for entry in runtime.timeline if entry["kind"] == "provider"]
    assert provider_sequences
    assert current_writes[0]["sequence"] > max(provider_sequences)
    feed_return = next(
        entry
        for entry in runtime.timeline
        if entry["kind"] == "feed_return" and entry["message_id"] == runtime.current_message_id
    )
    assert current_writes[0]["sequence"] < feed_return["sequence"]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("handler", ["audit", "ai:An audit message"])
async def test_filter_first_delivery_no_duplicate(audit_runtime: AuditRuntime, handler: str) -> None:
    await install_filter(audit_runtime, handler)
    await audit_runtime.feed(101, f"audit {SENTINEL}")
    scenario = "ai_filter" if handler.startswith("ai:") else "lexical_filter"
    assert [call["purpose"] for call in audit_runtime.calls] == (
        ["matching", "filter_reply"] if scenario == "ai_filter" else ["filter_reply"]
    )
    await assert_source_and_cache_counts(audit_runtime, read_counts=[0])
    assert_current_cached_after_providers(audit_runtime)
    assert_telegram_boundary(audit_runtime)
    if scenario == "ai_filter":
        assert_no_duplicate(audit_runtime, "matching", cache_read_records=0)
    assert_no_duplicate(audit_runtime, "filter_reply", cache_read_records=0)


@pytest.mark.asyncio(loop_scope="session")
async def test_ai_command_first_delivery_no_duplicate(audit_runtime: AuditRuntime) -> None:
    await audit_runtime.feed(102, f"/ai {SENTINEL}", command=True)
    assert [call["purpose"] for call in audit_runtime.calls] == ["command"]
    await assert_source_and_cache_counts(audit_runtime, read_counts=[0])
    assert_current_cached_after_providers(audit_runtime)
    reads = [entry for entry in audit_runtime.timeline if entry["kind"] == "cache_read"]
    assert reads and all(not entry["messages"] for entry in reads)
    assert_telegram_boundary(audit_runtime)
    assert_no_duplicate(audit_runtime, "command", cache_read_records=0)


@pytest.mark.asyncio(loop_scope="session")
async def test_earlier_module_command_cached_after_handling(audit_runtime: AuditRuntime) -> None:
    routers = [router.name for router in audit_runtime.dispatcher.sub_routers]
    assert routers.index("users") < routers.index("ai")
    request_offset = len(audit_runtime.capture_requests.all_requests)
    await audit_runtime.feed(103, "/id", command=True)
    assert audit_runtime.errors == [], f"Harness/dispatch errors: {audit_runtime.errors}"
    assert audit_runtime.calls == []
    responses = [
        request.response
        for request in audit_runtime.capture_requests.all_requests[request_offset:]
        if isinstance(request.response, Message)
    ]
    assert len(responses) == 1
    assert str(USER_TID) in message_text(responses[0])
    assert str(CHAT_TID) in message_text(responses[0])
    await assert_source_and_cache_counts(audit_runtime, read_counts=[])
    current_write = next(
        entry
        for entry in audit_runtime.timeline
        if entry["kind"] == "cache_write" and entry["message"]["message_id"] == 103
    )
    response_sequence = max(
        entry["sequence"] for entry in audit_runtime.timeline if entry["kind"] == "telegram_response"
    )
    assert current_write["sequence"] > response_sequence
    assert current_write["sequence"] < audit_runtime.timeline[-1]["sequence"]


@pytest.mark.asyncio(loop_scope="session")
async def test_natural_proactive_batch_no_duplicate(audit_runtime: AuditRuntime) -> None:
    chat = await ChatModel.get_by_tid(CHAT_TID)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    for message_id in range(201, 216):
        text = f"Group conversation message {message_id}"
        if message_id == 215:
            text = f"Can Sophie help with {SENTINEL}?"
        await audit_runtime.feed(message_id, text)
        if message_id < 215:
            assert audit_runtime.calls == []
    assert [call["purpose"] for call in audit_runtime.calls] == ["decision", "proactive_answer"]
    writes = [entry for entry in audit_runtime.timeline if entry["kind"] == "cache_write"]
    await assert_source_and_cache_counts(audit_runtime, read_counts=[1, 1])
    reads = [entry for entry in audit_runtime.timeline if entry["kind"] == "cache_read"]
    for message_id in range(201, 216):
        assert sum(message.message_id == message_id for message in audit_runtime.inputs) == 1
        assert sum(entry["message"]["message_id"] == message_id for entry in writes) == 1
    assert [[message["message_id"] for message in entry["messages"]] for entry in reads] == [
        list(range(201, 216)),
        list(range(201, 216)),
    ]
    assert_telegram_boundary(audit_runtime)
    assert_no_duplicate(audit_runtime, "decision", cache_read_records=1)
    decision = audit_runtime.calls[0]
    assert [
        identity["message_id"] for identity in decision["telegram_identities"] if identity["occurrences"] == 1
    ] == list(range(201, 216))
    assert_no_duplicate(audit_runtime, "proactive_answer", cache_read_records=2)
    answer = audit_runtime.calls[1]
    assert [
        identity["message_id"] for identity in answer["telegram_identities"] if identity["occurrences"] == 1
    ] == list(range(201, 216))
