from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory
from aiogram_test_framework.types import CapturedRequest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import (
    AIEvidenceMemoryModel,
    MemoryAction,
    MemoryActions,
    MemoryFact,
    MemorySource,
    MemoryState,
)
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.callbacks import AIMemoryPageCallback
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages, get_message_cache_key
from sophie_bot.modules.ai.utils.evidence_memory import apply_actions, capture_source, mutate_memory, read_memory
from sophie_bot.modules.help.callbacks import PMHelpModule
from sophie_bot.modules.help.utils.extract_info import gather_module_help
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import list_all
from sophie_bot.utils.pagination import telegram_html_length
from tests.e2e.helpers import _feed, create_test_user_and_group, grant_admin, send_reply_command, set_feature
from tests.e2e.test_ai_filter_reply_pipeline import _telegram_rich_response
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


def _button(requests: list[CapturedRequest], label: str) -> InlineKeyboardButton:
    return next(
        button
        for request in requests
        if request.reply_markup
        for row in InlineKeyboardMarkup.model_validate(request.reply_markup).inline_keyboard
        for button in row
        if button.text == label
    )


def _callback(requests: list[CapturedRequest], label: str) -> str:
    data = _button(requests, label).callback_data
    assert data is not None
    return data


def _message_text(requests: list[CapturedRequest]) -> str:
    return "\n".join(request.params.get("rich_message", {}).get("html") or request.text or "" for request in requests)


def _bot_message(chat: Chat) -> Message:
    bot = UserFactory.create(user_id=42, first_name="Sophie", is_bot=True)
    return MessageFactory.create(text="AI memory", from_user=bot, chat=chat)


async def test_silent_capture_controls_and_chat_boundaries(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_writer", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await _feed(
        test_client,
        MessageFactory.create(
            text="I prefer brief replies.", from_user=user, chat=group, date=datetime.now(UTC).replace(microsecond=0)
        ),
    )
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document is None  # Capture writes no MongoDB document.
    state = await read_memory(chat, datetime.now(UTC), redis=test_services.redis)
    assert any(source.text == "I prefer brief replies." for source in state.sources)
    message = _bot_message(group)
    browser = await test_client.send_command(command="ai_memory", from_user=user, chat=group)
    requests = await test_client.send_callback(_callback(browser, "Sources"), from_user=user, message=message)
    assert "<li>I prefer brief replies.</li>" in _message_text(requests)
    await AIMemoryModel.append_line(chat, "Keep legacy memory")
    summary_date = datetime.now(UTC).date()
    await AIChatSummaryModel.upsert_for_date(chat, summary_date, "Keep summary", [])
    privacy = _callback(browser, "Privacy")
    controls = await test_client.send_callback(privacy, from_user=user, message=message)
    assert all("Clear shared memory" not in str(request.reply_markup) for request in controls)
    for target in ("forget", "off"):
        forged = AIMemoryPageCallback.unpack(privacy).model_copy(update={"target": target}).pack()
        requests = await test_client.send_callback(forged, from_user=user, message=message)
        assert "Only chat administrators" in _message_text(requests)
    assert await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid) is None
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    assert await AIMemoryModel.get_lines(chat.iid) == ["Keep legacy memory"]
    assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is not None

    admin = await grant_admin(chat.tid, user.id)
    for legacy in ("show", "sources", "forget", "off", "on"):
        requests = await test_client.send_command(command=f"ai_memory {legacy}", from_user=user, chat=group)
        assert _message_text(requests) == "Open /ai_memory to browse memories and manage privacy."
        assert not any(request.reply_markup for request in requests)
    assert await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid) is None
    controls = await test_client.send_callback(privacy, from_user=user, message=message)
    stop = _callback(controls, "Stop saving my messages")
    prompt = await test_client.send_callback(stop, from_user=user, message=message)
    assert "for everyone" in _message_text(prompt)
    assert "stops AI memory capture for you" in _message_text(prompt)
    confirm = _callback(prompt, "Clear shared memory")
    assert await AIMemoryModel.get_lines(chat.iid) == ["Keep legacy memory"]
    assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is not None
    other_admin = test_client.create_user(first_name="Other admin").user
    await grant_admin(chat.tid, other_admin.id)
    requests = await test_client.send_callback(confirm, from_user=other_admin, message=message)
    assert "Only the requester" in _message_text(requests)
    await admin.delete()
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
    assert "Only chat administrators" in _message_text(requests)
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    await grant_admin(chat.tid, user.id)

    requests = await test_client.send_callback(_callback(prompt, "Cancel"), from_user=user, message=message)
    assert "canceled" in _message_text(requests)
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
    assert "no longer active" in _message_text(requests)
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources

    prompt = await test_client.send_callback(stop, from_user=user, message=message)
    confirm = _callback(prompt, "Clear shared memory")
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
    assert "AI memory capture is now off for you" in _message_text(requests)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and user.id in document.state.excluded_users
    assert not (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    assert not await AIMemoryModel.get_lines(chat.iid)
    assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is None
    requests = await test_client.send_command(command="ai_memory", from_user=user, chat=group)
    assert "Memory capture is off for you" in _message_text(requests)
    assert "No saved memories" in _message_text(requests)
    assert "Page" not in _message_text(requests)
    await _feed(
        test_client,
        MessageFactory.create(
            text="Do not retain me", from_user=user, chat=group, date=datetime.now(UTC).replace(microsecond=0)
        ),
    )
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and not (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    controls = await test_client.send_callback(_callback(requests, "Privacy"), from_user=user, message=message)
    requests = await test_client.send_callback(
        _callback(controls, "Resume saving my messages"), from_user=user, message=message
    )
    assert "Your messages can be saved again" in _message_text(requests)
    await _feed(
        test_client,
        MessageFactory.create(
            text="Capture resumed", from_user=user, chat=group, date=datetime.now(UTC).replace(microsecond=0)
        ),
    )
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len((await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources) == 1
    member = test_client.create_user(first_name="Member")
    await test_client.send_command(command="ai_reset", from_user=member.user, chat=group)
    # A regular member cannot reset the group's AI state.
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
    assert "no longer active" in _message_text(requests)
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    controls = await test_client.send_callback(privacy, from_user=user, message=message)
    prompt = await test_client.send_callback(
        _callback(controls, "Clear shared memory"), from_user=user, message=message
    )
    requests = await test_client.send_callback(
        _callback(prompt, "Clear shared memory"), from_user=user, message=message
    )
    assert "Shared AI memory and context were cleared" in _message_text(requests)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert (
        document
        and not (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
        and not document.state.facts
    )


async def test_delete_memory_by_id_is_atomic_and_forgets_all_versions(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    other = test_client.create_user(first_name="Other").user
    await _feed(test_client, MessageFactory.create(text="Other participant", from_user=other, chat=group))
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    now = datetime.now(UTC)

    def seed(state: MemoryState) -> None:
        for identity, speaker, text in ((91919, user, "Friday"), (91920, user, "Saturday"), (91921, other, "Tuesday")):
            capture_source(
                state,
                MemorySource(message_id=identity, speaker_tid=speaker.id, text=text, sent_at=now, branch_id=identity),
            )
            target = state.facts[0].fact_id if text == "Saturday" else None
            assert apply_actions(
                state,
                MemoryActions(
                    actions=[
                        MemoryAction(
                            action="UPDATE" if target else "ADD",
                            target_id=target,
                            speaker_tid=speaker.id,
                            event="picnic",
                            text=text,
                            evidence_ids=[identity],
                            subject_tids=[speaker.id],
                        )
                    ]
                ),
                {identity},
                state.generation,
                now,
            )

    state = await mutate_memory(chat, seed, redis=test_services.redis)
    first, latest, foreign = state.facts
    message = _bot_message(group)
    browser = await test_client.send_command(command="ai_memory", from_user=user, chat=group)
    details = await test_client.send_callback(_callback(browser, "1"), from_user=user, message=message)
    assert "Saturday" in _message_text(details)
    copy = _button(details, "Copy fact ID").copy_text
    assert copy and copy.text == latest.fact_id
    history = await test_client.send_callback(_callback(browser, "History"), from_user=user, message=message)
    assert "<li>Friday</li>" in _message_text(history) and "Saturday" not in _message_text(history)
    help_requests = await test_client.send_callback(_callback(browser, "Help"), from_user=user, message=message)
    assert "/ai_memory_delete FACT_ID" in _message_text(help_requests)
    assert "/ai_memory_correct" not in _message_text(help_requests)

    malformed = await test_client.send_command(command="ai_memory_delete", args="bad", from_user=user, chat=group)
    assert "valid memory fact IDs" in _message_text(malformed)
    denied = await test_client.send_command(
        command="ai_memory_delete", args=f"{latest.fact_id} {foreign.fact_id}", from_user=user, chat=group
    )
    assert "Only a chat administrator" in _message_text(denied)
    state = await read_memory(chat, now - timedelta(days=1), redis=test_services.redis)
    assert [fact.fact_id for fact in state.facts] == [first.fact_id, latest.fact_id, foreign.fact_id]

    deleted = await test_client.send_command(command="ai_memory_delete", args=first.fact_id, from_user=user, chat=group)
    assert "deleted the selected memories" in _message_text(deleted)
    state = await read_memory(chat, now - timedelta(days=1), redis=test_services.redis)
    assert [fact.fact_id for fact in state.facts] == [foreign.fact_id] and len(state.sources) == 3
    await grant_admin(chat.tid, user.id)
    deleted = await test_client.send_command(
        command="ai_memory_delete", args=foreign.fact_id, from_user=user, chat=group
    )
    assert "deleted the selected memories" in _message_text(deleted)
    state = await read_memory(chat, now - timedelta(days=1), redis=test_services.redis)
    assert not state.facts


@pytest.mark.parametrize("modern", [False, True])
async def test_chatbot_memory_edits_use_the_authenticated_requester(
    test_client: TestClient, test_services: ApplicationServices, modern: bool
) -> None:
    owner, group, _user_model = await create_test_user_and_group(test_client)
    editor = test_client.create_user(first_name="Editor").user
    await _feed(test_client, MessageFactory.create(text="Editor joined", from_user=editor, chat=group))
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    for feature in ("ai_memory_controls", "ai_memory_retrieval"):
        await set_feature(test_client, feature, True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_moderation", False, chat_tid=chat.tid)
    await set_feature(test_client, "ai_chatbot_modern_context", modern, chat_tid=chat.tid)
    arguments: dict[str, str | list[str]] = {"action": "add", "text": "I prefer tea", "event": "drinks"}

    def model_reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = {tool.name for tool in info.function_tools}
        assert {"search_memory", "manage_memory"} <= tools
        assert not {"write_memory", "forget_memory"} & tools
        last = messages[-1]
        if isinstance(last, ModelRequest):
            for part in last.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "manage_memory":
                    return ModelResponse(parts=[TextPart(str(part.content))])
        return ModelResponse(parts=[ToolCallPart("manage_memory", arguments.copy())])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        for position, part in enumerate(model_reply(messages, info).parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    position: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                    )
                }

    model = FunctionModel(model_reply, stream_function=stream, model_name="gpt-4o")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name, context_window_tokens=32000)])
    cutoff = datetime.now(UTC) - timedelta(days=1)
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack, bind_bot=True)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.utils.ai_chatbot_reply.get_chat_default_model_plan", AsyncMock(return_value=plan)
            )
        )
        stack.enter_context(patch("sophie_bot.modules.ai.utils.ai_run.get_ai_model", return_value=model))
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))

        requests = await test_client.send_command(
            command="ai", args="Remember that I prefer tea", from_user=owner, chat=group
        )
        assert "I saved that memory" in _message_text(requests)
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        (first,) = state.facts
        assert first.speaker_tid == owner.id and first.subject_tids == [owner.id]
        assert any(
            source.message_id in first.evidence_ids
            and source.text.endswith("Remember that I prefer tea")
            and source.processed
            for source in state.sources
        )

        arguments = {"action": "add", "text": "Likes coffee", "event": "drinks", "person": f"tg://user?id={owner.id}"}
        denied = await test_client.send_command(
            command="ai", args="Save a fact for someone else", from_user=editor, chat=group
        )
        assert "Only a chat administrator can add" in _message_text(denied)

        arguments = {"action": "update", "text": "Prefers coffee", "fact_ids": [first.fact_id]}
        denied = await test_client.send_command(
            command="ai", args="Correct the other person's fact", from_user=editor, chat=group
        )
        assert "Only a chat administrator can update" in _message_text(denied)
        arguments = {"action": "delete", "fact_ids": [first.fact_id]}
        denied = await test_client.send_command(
            command="ai", args="Delete the other person's fact", from_user=editor, chat=group
        )
        assert "Only a chat administrator can delete" in _message_text(denied)
        assert (await read_memory(chat, cutoff, redis=test_services.redis)).facts == [first]

        arguments = {"action": "update", "text": "I prefer coffee", "fact_ids": [first.fact_id]}
        updated = await test_client.send_command(command="ai", args="I prefer coffee now", from_user=owner, chat=group)
        assert "previous version is still in history" in _message_text(updated)
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        own_edit = state.facts[-1]
        assert own_edit.previous_id == first.fact_id and own_edit.editor_tid is None

        await grant_admin(chat.tid, editor.id)
        arguments = {"action": "update", "text": "Prefers mint tea", "fact_ids": [own_edit.fact_id]}
        updated = await test_client.send_command(
            command="ai", args="Correct the tea fact", from_user=editor, chat=group
        )
        assert "previous version is still in history" in _message_text(updated)
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        corrected = state.facts[-1]
        assert corrected.speaker_tid == owner.id and corrected.editor_tid == editor.id
        assert corrected.previous_id == own_edit.fact_id and corrected.text == "Prefers mint tea"

        arguments = {"action": "add", "text": "Likes walks", "event": "hobbies", "person": f"tg://user?id={owner.id}"}
        added = await test_client.send_command(
            command="ai", args="Remember that the other member likes walks", from_user=editor, chat=group
        )
        assert "I saved that memory" in _message_text(added)
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        observation = state.facts[-1]
        assert observation.speaker_tid == editor.id and observation.subject_tids == [owner.id]
        assert observation.layer == "observation"

        arguments = {"action": "delete", "fact_ids": [corrected.fact_id, observation.fact_id]}
        deleted = await test_client.send_command(command="ai", args="Forget the tea fact", from_user=editor, chat=group)
        assert "I deleted the selected memories" in _message_text(deleted)
        assert not (await read_memory(chat, cutoff, redis=test_services.redis)).facts


@pytest.mark.parametrize("controls_enabled", [False, True])
async def test_memory_commands_stay_out_of_cache(
    test_client: TestClient,
    test_services: ApplicationServices,
    controls_enabled: bool,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await set_feature(test_client, "ai_memory_writer", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_controls", controls_enabled, chat_tid=chat.tid)
    await _feed(
        test_client,
        MessageFactory.create(
            text="Existing behavior", from_user=user, chat=group, date=datetime.now(UTC).replace(microsecond=0)
        ),
    )
    await test_client.send_command(command="ai_memory_delete", args="0" * 32, from_user=user, chat=group)
    await test_client.send_command(command="AI-MEMORY private search text", from_user=user, chat=group)
    await test_client.send_command(command="ai_reset", from_user=user, chat=group)
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    assert [message.text for message in cached] == ["Existing behavior"]
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document is None


async def test_help_shows_memory_commands_in_one_section(
    test_client: TestClient, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = test_client.create_user(first_name="Reader").user
    private_chat = ChatFactory.create_private_from_user(user)
    await set_feature(test_client, "ai_memory_controls", True)
    registry = test_services.modules
    ai_help = await gather_module_help(registry.modules["ai"], await list_all(redis=test_services.redis), {})
    assert ai_help is not None
    monkeypatch.setitem(registry.help_modules, "ai", ai_help)
    requests = await test_client.send_callback(
        PMHelpModule(module_name="ai", back_to_start=False).pack(), from_user=user, message=_bot_message(private_chat)
    )
    html = next(request.params["rich_message"]["html"] for request in requests if request.params.get("rich_message"))
    leading, heading, remainder = html.partition("<h2>Memory</h2>")
    assert heading and "/ai_memory" not in leading
    memory, _, following = remainder.partition("<h2>")
    assert "/ai_memory" not in following
    assert "<code>/ai_memory</code>" in memory
    assert "<code>/ai_memory_delete</code>" in memory
    assert "/ai_memory_correct" not in memory
    assert memory.count("<code>/ai_memory") == 2


async def test_browse_memories_by_mention_reply_or_search(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    member = test_client.create_user(first_name="Sam <3", username="Sample_User").user
    user_message = MessageFactory.create(text="Hello", from_user=member, chat=group)
    await _feed(test_client, user_message)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    now = datetime.now(UTC)

    def seed(state: MemoryState) -> None:
        for speaker_tid, subject_tid, text in (
            (user.id, user.id, "My plan for 2042"),
            (member.id, member.id, "Sam prefers tea"),
            (user.id, member.id, "Sam enjoys hiking"),
        ):
            state.facts.append(
                MemoryFact(
                    speaker_tid=speaker_tid,
                    subject_tids=[subject_tid],
                    scope="person",
                    layer="personal" if speaker_tid == subject_tid else "observation",
                    kind="fact",
                    event="preferences",
                    text=text,
                    topic_id=None,
                    branch_ids=[],
                    evidence_ids=[12345],
                    recorded_at=now,
                    occurred_at=now,
                )
            )

    await mutate_memory(chat, seed, redis=test_services.redis)
    username_reply = await test_client.send_command(command="ai_memory @sample_user", from_user=user, chat=group)
    listed = _message_text(username_reply)
    assert "AI memory for Sam &lt;3" in listed and "Sam prefers tea" in listed and "Sam enjoys hiking" in listed
    assert "My plan" not in listed
    buttons = next(request.reply_markup["inline_keyboard"] for request in username_reply if request.reply_markup)
    assert all(button["text"] not in {"History", "Sources"} for row in buttons for button in row)

    quoted = await send_reply_command(
        test_client, command="ai_memory", from_user=user, group=group, replied=user_message
    )
    assert _message_text(quoted) == listed
    mentioned = await _feed(
        test_client,
        MessageFactory.create(
            text="/ai_memory Sam <3",
            from_user=user,
            chat=group,
            entities=[MessageEntity(type="text_mention", offset=11, length=6, user=member)],
        ),
    )
    assert _message_text(mentioned) == listed

    callback = _callback(username_reply, "1")
    bot_message = _bot_message(group)
    details = await test_client.send_callback(callback, from_user=user, message=bot_message)
    assert "Sam enjoys hiking" in _message_text(details) and "Source messages" not in _message_text(details)
    buttons = next(request.reply_markup["inline_keyboard"] for request in details if request.reply_markup)
    assert [button["text"] for row in buttons for button in row] == ["Copy fact ID", "Back to list"]
    back = await test_client.send_callback(_callback(details, "Back to list"), from_user=user, message=bot_message)
    assert _message_text(back).strip() == listed.strip()
    forged_sources = AIMemoryPageCallback.unpack(callback).model_copy(update={"view": "sources", "target": ""}).pack()
    requests = await test_client.send_callback(forged_sources, from_user=user, message=bot_message)
    assert "only shows the member" in _message_text(requests)

    search = await send_reply_command(
        test_client, command="ai_memory", args="2042", from_user=user, group=group, replied=user_message
    )
    assert "Search: 2042" in _message_text(search) and "My plan for 2042" in _message_text(search)
    search = await test_client.send_command(command="ai_memory @sample_user notes", from_user=user, chat=group)
    assert "Search: @sample_user notes" in _message_text(search)
    for query in ("off topic", "correct billing errors"):
        search = await test_client.send_command(command=f"ai_memory {query}", from_user=user, chat=group)
        assert f"Search: {query}" in _message_text(search)
    unknown = await test_client.send_command(command="ai_memory @unknown_user", from_user=user, chat=group)
    assert "could not find that username" in _message_text(unknown)


async def test_memory_buttons_paginate_and_reject_other_users(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    now = datetime.now(UTC)
    query = "🪴" * 120
    long_source = "Source 19\n" + "🪴" * 3000 + "\n<source end>"

    def seed(state: MemoryState) -> None:
        for identity in range(1, 20):
            recorded_at = now - timedelta(seconds=19 - identity)
            capture_source(
                state,
                MemorySource(
                    message_id=identity,
                    speaker_tid=user.id,
                    text=long_source if identity == 19 else f"Source {identity}",
                    sent_at=recorded_at,
                    branch_id=identity,
                ),
            )
            apply_actions(
                state,
                MemoryActions(
                    actions=[
                        MemoryAction(
                            action="ADD",
                            speaker_tid=user.id,
                            subject_tids=[user.id],
                            event="preferences",
                            text=f"Fact {identity} {query * 4}" if identity < 19 else "Unrelated Fact 19",
                            evidence_ids=[identity],
                        )
                    ]
                ),
                {identity},
                state.generation,
                recorded_at,
            )

    await mutate_memory(chat, seed, redis=test_services.redis)
    message = _bot_message(group)
    requests = await test_client.send_command(command=f"ai_memory {query}", from_user=user, chat=group)
    text = _message_text(requests)
    assert f"<ol><li>Fact 18 {query * 4}</li>" in text
    assert "Fact 3" not in text and "Unrelated" not in text
    assert "Page 1 of 2" in text and 4096 < telegram_html_length(text)
    assert len(text.encode("utf-8")) <= 30_000 and 8 < text.count("<li>") < 16
    payload = next(request for request in requests if request.params.get("rich_message"))
    assert payload.params["reply_parameters"]["message_id"]
    assert all(len(row) <= 8 for row in payload.reply_markup["inline_keyboard"])
    callback = _callback(requests, "Next ▶️")
    assert AIMemoryPageCallback.unpack(callback).page == 1
    page_two = await test_client.send_callback(callback, from_user=user, message=message)
    text = _message_text(page_two)
    assert f"Search: {query}" in text and "Fact 3" in text and "Fact 18" not in text
    assert "Page 2 of 2" in text
    other = test_client.create_user(first_name="Other")
    requests = await test_client.send_callback(callback, from_user=other.user, message=message)
    assert "another user" in _message_text(requests)

    sources = await test_client.send_callback(_callback(page_two, "Sources"), from_user=user, message=message)
    assert _message_text(sources).count("Source 19") == 1
    assert "<source end>" not in _message_text(sources)
    source_callback = _callback(sources, "1")
    details = await test_client.send_callback(source_callback, from_user=user, message=message)
    source_html = _message_text(details)
    assert "Page " not in source_html and "Source 19<br>" in source_html
    assert "&lt;source end&gt;" in source_html
    assert 4096 < telegram_html_length(source_html) and len(source_html.encode("utf-8")) <= 30_000
    sources = await test_client.send_callback(_callback(details, "Back to list"), from_user=user, message=message)
    requests = await test_client.send_callback(_callback(sources, "Memories"), from_user=user, message=message)
    assert f"Search: {query}" in _message_text(requests) and "Unrelated" not in _message_text(requests)
    fact_callback = _callback(requests, "1")
    await test_services.redis.delete(get_message_cache_key(chat.tid))
    requests = await test_client.send_callback(source_callback, from_user=user, message=message)
    assert "expired or is unavailable" in _message_text(requests)
    requests = await test_client.send_callback(fact_callback, from_user=user, message=message)
    assert "Source messages expired or unavailable" in _message_text(requests)
    await grant_admin(chat.tid, user.id)
    browser = await test_client.send_command(command="ai_memory", from_user=user, chat=group)
    controls = await test_client.send_callback(_callback(browser, "Privacy"), from_user=user, message=message)
    prompt = await test_client.send_callback(
        _callback(controls, "Clear shared memory"), from_user=user, message=message
    )
    await test_client.send_callback(_callback(prompt, "Clear shared memory"), from_user=user, message=message)
    requests = await test_client.send_callback(callback, from_user=user, message=message)
    assert "No memories match your search" in _message_text(requests)
    requests = await test_client.send_callback(fact_callback, from_user=user, message=message)
    assert "This memory is no longer in this list" in _message_text(requests)
