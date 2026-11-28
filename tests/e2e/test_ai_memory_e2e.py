from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, User
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
from sophie_bot.modules.ai.callbacks import AIMemoryEditCallback, AIMemoryPageCallback
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _send_memory_edit_confirmation
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages, get_context_epoch_key, get_message_cache_key
from sophie_bot.modules.ai.utils.evidence_memory import apply_actions, capture_source, mutate_memory, read_memory
from sophie_bot.modules.ai.utils.memory_edits import MemoryEditProposal, MemoryEditRequest, preview_memory_edit
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


def _rich_callbacks(requests: list[CapturedRequest], label: str) -> list[str]:
    return [
        unescape(data)
        for data in re.findall(
            rf'<tg-button type="callback_data" data="([^"]+)"[^>]*>{re.escape(label)}</tg-button>',
            _message_text(requests),
        )
    ]


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
    requests = await test_client.send_command(command="ai_memory --sources", from_user=user, chat=group)
    assert "<li><p>I prefer brief replies." in _message_text(requests)
    await AIMemoryModel.append_line(chat, "Keep legacy memory")
    summary_date = datetime.now(UTC).date()
    await AIChatSummaryModel.upsert_for_date(chat, summary_date, "Keep summary", [])
    controls = await test_client.send_command(command="ai_memory --privacy", from_user=user, chat=group)
    assert "You allow AI memory capture" in _message_text(controls)
    assert not _rich_callbacks(controls, "Clear shared memory")
    admin = await grant_admin(chat.tid, user.id)
    controls = await test_client.send_command(command="ai_memory --privacy", from_user=user, chat=group)
    (stop,) = _rich_callbacks(controls, "Stop saving my messages")
    (clear,) = _rich_callbacks(controls, "Clear shared memory")
    await admin.delete()
    for action in (clear, stop):
        requests = await test_client.send_callback(action, from_user=user, message=message)
        assert "Only chat administrators" in _message_text(requests)
    assert await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid) is None
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
    assert await AIMemoryModel.get_lines(chat.iid) == ["Keep legacy memory"]
    assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is not None

    admin = await grant_admin(chat.tid, user.id)
    for legacy in ("show", "sources", "forget", "off", "on"):
        requests = await test_client.send_command(command=f"ai_memory {legacy}", from_user=user, chat=group)
        assert _message_text(requests) == "Use /ai_memory to browse memories or /ai_memory --privacy for privacy."
        assert not any(request.reply_markup for request in requests)
    assert await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid) is None
    prompt = await test_client.send_callback(stop, from_user=user, message=message)
    assert "for everyone" in _message_text(prompt)
    assert "stops AI memory capture for you" in _message_text(prompt)
    (confirm,) = _rich_callbacks(prompt, "Clear shared memory")
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

    (cancel,) = _rich_callbacks(prompt, "Cancel")
    requests = await test_client.send_callback(cancel, from_user=user, message=message)
    assert "canceled" in _message_text(requests)
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
    assert "no longer active" in _message_text(requests)
    assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources

    prompt = await test_client.send_callback(stop, from_user=user, message=message)
    (confirm,) = _rich_callbacks(prompt, "Clear shared memory")
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
    controls = await test_client.send_command(command="ai_memory --privacy", from_user=user, chat=group)
    (resume,) = _rich_callbacks(controls, "Resume saving my messages")
    requests = await test_client.send_callback(resume, from_user=user, message=message)
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
    prompt = await test_client.send_callback(clear, from_user=user, message=message)
    (confirm,) = _rich_callbacks(prompt, "Clear shared memory")
    requests = await test_client.send_callback(confirm, from_user=user, message=message)
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
    (detail,) = _rich_callbacks(browser, "Details")
    details = await test_client.send_callback(detail, from_user=user, message=message)
    assert "Saturday" in _message_text(details)
    assert f"<code>{latest.fact_id}</code>" in _message_text(details)
    assert not any(request.reply_markup for request in details)
    history = await test_client.send_command(command="ai_memory --history", from_user=user, chat=group)
    assert "<li><p>Friday" in _message_text(history) and "Saturday" not in _message_text(history)
    assert _rich_callbacks(history, "Details") and not _rich_callbacks(history, "Edit")

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
    invalidate_preview = False

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
        if invalidate_preview and any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            await test_services.redis.incr(get_context_epoch_key(chat.tid))
        for position, part in enumerate(model_reply(messages, info).parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    position: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                    )
                }

    async def confirm(requests: list[CapturedRequest], user: User) -> list[CapturedRequest]:
        token = _callback(requests, "Confirm memory change")
        return await test_client.send_callback(token, from_user=user, message=_bot_message(group))

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
        assert "Save this memory?" in _message_text(requests)
        assert not (await read_memory(chat, cutoff, redis=test_services.redis)).facts
        denied_confirmation = await confirm(requests, editor)
        assert "belongs to another user" in _message_text(denied_confirmation)
        assert not (await read_memory(chat, cutoff, redis=test_services.redis)).facts
        applied = await confirm(requests, owner)
        assert "I saved that memory" in _message_text(applied)
        reused = await confirm(requests, owner)
        assert "expired" in _message_text(reused)
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
        assert "previous version is still in history" in _message_text(await confirm(updated, owner))
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        own_edit = state.facts[-1]
        assert own_edit.previous_id == first.fact_id and own_edit.editor_tid is None

        admin = await grant_admin(chat.tid, editor.id)
        arguments = {"action": "delete", "fact_ids": [own_edit.fact_id]}
        invalidate_preview = True
        stale = await test_client.send_command(command="ai", args="Forget the tea fact", from_user=editor, chat=group)
        invalidate_preview = False
        assert "Memory changed while processing" in _message_text(stale)
        assert own_edit.fact_id not in _message_text(stale)
        assert not any(request.reply_markup for request in stale)
        unrelated = await test_client.send_command(
            command="ai", args="What can you help with?", from_user=editor, chat=group
        )
        assert "Delete these memories" in _message_text(unrelated)
        assert len((await read_memory(chat, cutoff, redis=test_services.redis)).facts) == 2
        await test_client.send_callback(_callback(unrelated, "Cancel"), from_user=editor, message=_bot_message(group))
        assert "expired" in _message_text(await confirm(unrelated, editor))
        assert len((await read_memory(chat, cutoff, redis=test_services.redis)).facts) == 2

        arguments = {"action": "update", "text": "Prefers mint tea", "fact_ids": [own_edit.fact_id]}
        revoked = await test_client.send_command(
            command="ai", args="Correct the tea fact", from_user=editor, chat=group
        )
        await admin.delete()
        assert "Only a chat administrator" in _message_text(await confirm(revoked, editor))
        await grant_admin(chat.tid, editor.id)
        updated = await test_client.send_command(
            command="ai", args="Correct the tea fact", from_user=editor, chat=group
        )
        assert "previous version is still in history" in _message_text(await confirm(updated, editor))
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        corrected = state.facts[-1]
        assert corrected.speaker_tid == owner.id and corrected.editor_tid == editor.id
        assert corrected.previous_id == own_edit.fact_id and corrected.text == "Prefers mint tea"

        arguments = {"action": "add", "text": "Likes walks", "event": "hobbies", "person": f"tg://user?id={owner.id}"}
        added = await test_client.send_command(
            command="ai", args="Remember that the other member likes walks", from_user=editor, chat=group
        )
        assert "I saved that memory" in _message_text(await confirm(added, editor))
        state = await read_memory(chat, cutoff, redis=test_services.redis)
        observation = state.facts[-1]
        assert observation.speaker_tid == editor.id and observation.subject_tids == [owner.id]
        assert observation.layer == "observation"

        arguments = {"action": "delete", "fact_ids": [corrected.fact_id, observation.fact_id]}
        deleted = await test_client.send_command(command="ai", args="Forget the tea fact", from_user=editor, chat=group)
        assert "I deleted the selected memories" in _message_text(await confirm(deleted, editor))
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
    assert "--history" in memory and "--sources" in memory and "--privacy" in memory
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
    assert not any(request.reply_markup for request in username_reply)
    edits = _rich_callbacks(username_reply, "Edit")
    details = _rich_callbacks(username_reply, "Details")
    assert len(edits) == len(details) == 2

    quoted = await send_reply_command(
        test_client, command="ai_memory", from_user=user, group=group, replied=user_message
    )
    assert re.sub(r' data="[^"]+"', "", _message_text(quoted)) == re.sub(r' data="[^"]+"', "", listed)
    mentioned = await _feed(
        test_client,
        MessageFactory.create(
            text="/ai_memory Sam <3",
            from_user=user,
            chat=group,
            entities=[MessageEntity(type="text_mention", offset=11, length=6, user=member)],
        ),
    )
    assert re.sub(r' data="[^"]+"', "", _message_text(mentioned)) == re.sub(r' data="[^"]+"', "", listed)

    callback = details[0]
    bot_message = _bot_message(group)
    result = await test_client.send_callback(callback, from_user=user, message=bot_message)
    assert "Sam enjoys hiking" in _message_text(result) and "Source messages" not in _message_text(result)
    payload = next(request for request in result if request.params.get("rich_message"))
    assert payload.params["reply_parameters"]["message_id"] == bot_message.message_id
    assert not payload.reply_markup
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


async def test_edit_memory_inline_uses_the_requesters_reply(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    other = test_client.create_user(first_name="Other").user
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_retrieval", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    now = datetime.now(UTC) - timedelta(minutes=1)
    fact = MemoryFact(
        speaker_tid=user.id,
        subject_tids=[user.id],
        scope="person",
        layer="personal",
        kind="fact",
        event="preferences",
        text="I like tea",
        topic_id=None,
        branch_ids=[],
        evidence_ids=[],
        recorded_at=now,
        occurred_at=now,
    )

    def seed(state: MemoryState) -> None:
        state.facts.append(fact)

    await mutate_memory(chat, seed, redis=test_services.redis)
    browser = await test_client.send_command(command="aimemory", from_user=user, chat=group)
    (edit,) = _rich_callbacks(browser, "Edit")
    message = _bot_message(group)
    denied = await test_client.send_callback(edit, from_user=other, message=message)
    assert "another user" in _message_text(denied)
    prompt_requests = await test_client.send_callback(edit, from_user=user, message=message)
    prompt_request = next(request for request in prompt_requests if "new memory text" in (request.text or ""))
    assert prompt_request.reply_markup == {"force_reply": True, "selective": True}
    assert isinstance(prompt_request.response, Message)
    prompt = prompt_request.response.model_copy(update={"chat": group})
    await _feed(
        test_client,
        MessageFactory.create(text="Not my memory", from_user=other, chat=group, reply_to_message=prompt),
    )
    state = await read_memory(chat, now - timedelta(days=1), redis=test_services.redis)
    assert [item.fact_id for item in state.facts] == [fact.fact_id]
    result = await _feed(
        test_client,
        MessageFactory.create(text="I like <green> tea", from_user=user, chat=group, reply_to_message=prompt),
    )
    assert "I updated that memory" in _message_text(result)
    state = await read_memory(chat, now - timedelta(days=1), redis=test_services.redis)
    original, current = state.facts
    assert original.fact_id == current.previous_id == fact.fact_id
    assert current.text == "I like <green> tea"
    stale = await test_client.send_callback(edit, from_user=user, message=message)
    assert "no longer in this list" in _message_text(stale)
    browser = await test_client.send_command(command="aimemory", from_user=user, chat=group)
    assert "I like &lt;green&gt; tea" in _message_text(browser)


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
    assert f"<ol><li><p>Fact 18 {query * 4} <tg-button" in text
    assert "Fact 3" not in text and "Unrelated" not in text
    assert "Page 1 of 2" in text and 4096 < telegram_html_length(text)
    assert len(text.encode("utf-8")) <= 30_000 and 8 < text.count("<li>") < 16
    payload = next(request for request in requests if request.params.get("rich_message"))
    assert payload.params["reply_parameters"]["message_id"]
    assert [[button["text"] for button in row] for row in payload.reply_markup["inline_keyboard"]] == [["Next ▶️"]]
    assert len(_rich_callbacks(requests, "Edit")) == len(_rich_callbacks(requests, "Details")) == text.count("<li>")
    callback = _callback(requests, "Next ▶️")
    assert AIMemoryPageCallback.unpack(callback).page == 1
    page_two = await test_client.send_callback(callback, from_user=user, message=message)
    text = _message_text(page_two)
    assert f"Search: {query}" in text and "Fact 3" in text and "Fact 18" not in text
    assert "Page 2 of 2" in text
    other = test_client.create_user(first_name="Other")
    requests = await test_client.send_callback(callback, from_user=other.user, message=message)
    assert "another user" in _message_text(requests)

    sources = await test_client.send_command(command="ai_memory --sources", from_user=user, chat=group)
    assert _message_text(sources).count("Source 19") == 1
    assert "<source end>" not in _message_text(sources)
    source_callback = _rich_callbacks(sources, "Details")[0]
    details = await test_client.send_callback(source_callback, from_user=user, message=message)
    source_html = _message_text(details)
    assert "Page " not in source_html and "Source 19<br>" in source_html
    assert "&lt;source end&gt;" in source_html
    assert 4096 < telegram_html_length(source_html) and len(source_html.encode("utf-8")) <= 30_000
    fact_callback = _rich_callbacks(page_two, "Details")[0]
    await test_services.redis.delete(get_message_cache_key(chat.tid))
    requests = await test_client.send_callback(source_callback, from_user=user, message=message)
    assert "expired or is unavailable" in _message_text(requests)
    requests = await test_client.send_callback(fact_callback, from_user=user, message=message)
    assert "Source messages expired or unavailable" in _message_text(requests)
    await grant_admin(chat.tid, user.id)
    controls = await test_client.send_command(command="ai_memory --privacy", from_user=user, chat=group)
    (clear,) = _rich_callbacks(controls, "Clear shared memory")
    prompt = await test_client.send_callback(clear, from_user=user, message=message)
    (confirm,) = _rich_callbacks(prompt, "Clear shared memory")
    await test_client.send_callback(confirm, from_user=user, message=message)
    requests = await test_client.send_callback(callback, from_user=user, message=message)
    assert "No memories match your search" in _message_text(requests)
    requests = await test_client.send_callback(fact_callback, from_user=user, message=message)
    assert "This memory is no longer in this list" in _message_text(requests)


@pytest.mark.parametrize("action", ["confirm", "cancel"])
@pytest.mark.parametrize(
    "clicker",
    [
        "caller",
        "admin",
        "member",
        "subject",
        "revoked",
        "other_chat_admin",
        "operator",
        "expired",
        "missing",
        "cross_chat",
        "wrong_topic",
        "forged",
    ],
)
async def test_memory_proposal_callback_authorization_and_lifecycle(
    test_client: TestClient,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    action: Literal["confirm", "cancel"],
    clicker: str,
) -> None:
    caller, group, _user_model = await create_test_user_and_group(test_client)
    other, other_group, _other_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    for feature in ("ai_memory_controls", "ai_memory_retrieval"):
        await set_feature(test_client, feature, True, chat_tid=group.id)
        await set_feature(test_client, feature, True, chat_tid=other_group.id)
    # The caller proposes a fact about someone else: subject is not the owner.
    await grant_admin(group.id, caller.id)
    member = test_client.create_user(first_name="Member").user
    await _feed(test_client, MessageFactory.create(text="Joined", from_user=member, chat=group))
    await _feed(test_client, MessageFactory.create(text="Joined", from_user=other, chat=group))
    request_message = MessageFactory.create(text="Remember this", from_user=caller, chat=group).as_(test_client.bot)
    request = MemoryEditRequest.from_message(request_message, epoch=0)
    assert request is not None
    proposal = MemoryEditProposal(
        source=request.source,
        epoch=0,
        action="add",
        text="Likes tea",
        event="drinks",
        subject_tid=other.id,
    )
    subject_request = MemoryEditRequest.from_message(
        MessageFactory.create(text="I like tea", from_user=other, chat=group), epoch=0
    )
    assert subject_request is not None
    await mutate_memory(chat, lambda state: capture_source(state, subject_request.source), redis=test_services.redis)
    preview = await preview_memory_edit(chat, proposal, admin=True, redis=test_services.redis)
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack, bind_bot=True)
        test_client.capture.clear()
        await _send_memory_edit_confirmation(request_message, None, preview, chat=chat, services=test_services)
        requests = test_client.capture.all_requests
        original = _callback(requests, "Confirm memory change")
        cancel = _callback(requests, "Cancel")
        callback = original if action == "confirm" else cancel
        actor = caller if clicker == "caller" else member
        message = _bot_message(group)
        if clicker in {"admin", "revoked", "cross_chat", "wrong_topic", "forged", "expired", "missing"}:
            record = await grant_admin(group.id, member.id)
            if clicker == "revoked":
                await record.delete()
        if clicker == "subject":
            actor = other
        if clicker == "other_chat_admin":
            await grant_admin(other_group.id, member.id)
        if clicker == "operator":
            monkeypatch.setattr("sophie_bot.modules.utils_.admin.CONFIG.operators", [member.id])
        if clicker == "cross_chat":
            await grant_admin(other_group.id, member.id)
            message = _bot_message(other_group)
        if clicker == "wrong_topic":
            message = message.model_copy(update={"message_thread_id": 123})
        if clicker == "forged":
            callback = AIMemoryEditCallback(token="unknown-token", action=action).pack()
        if clicker in {"expired", "missing"}:
            keys = [key async for key in test_services.redis.scan_iter(match="ai:memory:edit:*")]
            assert len(keys) == 1
            if clicker == "expired":
                await test_services.redis.pexpire(keys[0], 0)
            else:
                await test_services.redis.delete(keys[0])
        cutoff = datetime.now(UTC) - timedelta(days=1)
        before = await read_memory(chat, cutoff, redis=test_services.redis)
        result = await test_client.send_callback(callback, from_user=actor, message=message)
        authorized = clicker in {"caller", "admin"}
        after = await read_memory(chat, cutoff, redis=test_services.redis)
        if authorized and action == "confirm":
            assert "I saved that memory" in _message_text(result)
            (fact,) = after.facts
            assert fact.speaker_tid == caller.id and fact.subject_tids == [other.id]
            assert fact.text == "Likes tea"
        else:
            assert after == before
            if authorized:
                assert "Memory change canceled" in _message_text(result)
            else:
                assert any(item.params.get("show_alert") for item in result)
        replay = await test_client.send_callback(original, from_user=caller, message=_bot_message(group))
        if authorized or clicker in {"expired", "missing"}:
            assert "expired" in _message_text(replay)
            assert await read_memory(chat, cutoff, redis=test_services.redis) == after
        else:
            # Rejections must leave the original proposal available to its caller.
            assert "I saved that memory" in _message_text(replay)
            assert len((await read_memory(chat, cutoff, redis=test_services.redis)).facts) == 1
        other_chat = await ChatModel.get_by_tid(other_group.id)
        assert other_chat is not None
        assert not (await read_memory(other_chat, cutoff, redis=test_services.redis)).facts
        markup = next(InlineKeyboardMarkup.model_validate(item.reply_markup) for item in requests if item.reply_markup)
        assert [len(row) for row in markup.inline_keyboard] == [1, 1]
        assert [button.text for row in markup.inline_keyboard for button in row] == ["Confirm memory change", "Cancel"]
        assert AIMemoryEditCallback.unpack(original).token == AIMemoryEditCallback.unpack(cancel).token
