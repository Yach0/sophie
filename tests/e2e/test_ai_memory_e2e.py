from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory
from aiogram_test_framework.types import CapturedRequest

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
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages, get_message_cache_key
from sophie_bot.modules.ai.utils.evidence_memory import apply_actions, capture_source, mutate_memory, read_memory
from sophie_bot.modules.help.callbacks import PMHelpModule
from sophie_bot.modules.help.utils.extract_info import gather_module_help
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import list_all
from sophie_bot.utils.pagination import telegram_html_length
from tests.e2e.helpers import _feed, create_test_user_and_group, grant_admin, send_reply_command, set_feature


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
    await set_feature(test_client, "ai_memory_capture", True, chat_tid=chat.tid)
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
    requests = await test_client.send_command(command="ai_memory_sources", from_user=user, chat=group)
    assert "<li>I prefer brief replies.</li>" in _message_text(requests)
    await AIMemoryModel.append_line(chat, "Keep legacy memory")
    summary_date = datetime.now(UTC).date()
    await AIChatSummaryModel.upsert_for_date(chat, summary_date, "Keep summary", [])
    for operation in ("forget", "off"):
        requests = await test_client.send_command(command="ai_memory_" + operation, from_user=user, chat=group)
        assert any("Only chat administrators" in (request.text or "") for request in requests)
        document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
        assert document is None
        assert (await read_memory(chat, datetime.now(UTC), redis=test_services.redis)).sources
        assert await AIMemoryModel.get_lines(chat.iid) == ["Keep legacy memory"]
        assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is not None
    admin = await grant_admin(chat.tid, user.id)
    message = _bot_message(group)
    requests = await test_client.send_command(command="ai_memory off", from_user=user, chat=group)
    assert "Search: off" in _message_text(requests)
    assert "for everyone" not in _message_text(requests)
    prompt = await test_client.send_command(command="ai_memory_off", from_user=user, chat=group)
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

    prompt = await test_client.send_command(command="ai_memory_off", from_user=user, chat=group)
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
    requests = await test_client.send_command(command="ai_memory_on", from_user=user, chat=group)
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
    prompt = await test_client.send_command(command="ai_memory_forget", from_user=user, chat=group)
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


async def test_personal_correction_ownership_and_invalid_arguments(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    group = group.model_copy(update={"type": "supergroup", "is_forum": True})
    chat = await ChatModel.upsert_group(group)
    other = test_client.create_user(first_name="Other")
    await set_feature(test_client, "ai_memory_capture", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    now = datetime.now(UTC)
    topic_id = 91918

    def seed(state: MemoryState) -> None:
        capture_source(
            state,
            MemorySource(
                message_id=91919,
                speaker_tid=user.id,
                text="Friday",
                sent_at=now,
                branch_id=91919,
                topic_id=topic_id,
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
                        event="picnic",
                        text="Friday",
                        evidence_ids=[91919],
                    )
                ]
            ),
            {91919},
            state.generation,
            now,
        )

    await mutate_memory(chat, seed, redis=test_services.redis)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document
    fact_id = document.state.facts[0].fact_id
    wrong_topic = await test_client.send_command(
        command=f"ai_memory_correct {fact_id} Saturday", from_user=user, chat=group
    )
    assert "original topic" in _message_text(wrong_topic)
    correction = MessageFactory.create_command(
        "ai_memory_correct", args=f"{fact_id} Saturday\n<after lunch>", from_user=user, chat=group
    ).model_copy(update={"message_thread_id": topic_id, "is_topic_message": True})
    await _feed(test_client, correction.model_copy(update={"from_user": other.user}))
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.facts) == 1
    await _feed(test_client, correction)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.facts) == 2
    assert document.state.facts[1].text == "Saturday\n<after lunch>"
    assert document.state.facts[1].previous_id == fact_id
    requests = await test_client.send_command(command="ai_memory", from_user=user, chat=group)
    text = _message_text(requests)
    assert "<li>Saturday &lt;after lunch&gt;</li>" in text
    assert all(metadata not in text for metadata in ("<b>ID</b>", "Source messages", fact_id, "Friday", "Page"))
    message = _bot_message(group)
    details = await test_client.send_callback(_callback(requests, "1"), from_user=user, message=message)
    text = _message_text(details)
    assert "Saturday<br>&lt;after lunch&gt;" in text
    assert fact_id in text and "Source messages" in text
    copy = _button(details, "Copy correction command").copy_text
    assert copy and copy.text == f"/ai_memory_correct {document.state.facts[1].fact_id} "
    assert f"https://t.me/c/{str(chat.tid).removeprefix('-100')}/{topic_id}" in text
    back = await test_client.send_callback(_callback(details, "Back to list"), from_user=user, message=message)
    assert "<li>Saturday" in _message_text(back)
    history = await test_client.send_callback(_callback(back, "History"), from_user=user, message=message)
    assert "<li>Friday</li>" in _message_text(history) and "Saturday" not in _message_text(history)
    requests = await test_client.send_command(command="ai_memory_history", from_user=user, chat=group)
    assert _message_text(requests).strip() == _message_text(history).strip()
    previous = await test_client.send_callback(_callback(history, "1"), from_user=user, message=message)
    assert all(
        button["text"] != "Copy correction command"
        for request in previous
        if request.reply_markup
        for row in request.reply_markup["inline_keyboard"]
        for button in row
    )
    help_requests = await test_client.send_callback(_callback(history, "Help"), from_user=user, message=message)
    text = _message_text(help_requests)
    assert "/ai_memory" in text
    assert "/ai_memory_sources" in text
    assert "/ai_memory_correct FACT_ID I prefer short replies." in text
    assert "/ai_memory_off" in text and "/ai_memory_on" in text
    requests = await test_client.send_command(command="ai_memory_help", from_user=user, chat=group)
    assert _message_text(requests).strip() == text.strip()
    requests = await test_client.send_command(command="ai_memory invalid", from_user=user, chat=group)
    assert "No memories match your search" in _message_text(requests)


async def test_disabled_controls_leave_memory_commands_out_of_cache(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await set_feature(test_client, "ai_memory_capture", True, chat_tid=chat.tid)
    await _feed(
        test_client,
        MessageFactory.create(
            text="Existing behavior", from_user=user, chat=group, date=datetime.now(UTC).replace(microsecond=0)
        ),
    )
    await test_client.send_command(command="ai_memory_off", from_user=user, chat=group)
    await test_client.send_command(command="AI-MEMORY private search text", from_user=user, chat=group)
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    assert any(message.text == "Existing behavior" for message in cached)
    assert all("private search text" not in message.text and "ai_memory_off" not in message.text for message in cached)
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
    for command in (
        "ai_memory",
        "ai_memory_history",
        "ai_memory_sources",
        "ai_memory_correct",
        "ai_memory_on",
        "ai_memory_help",
    ):
        assert f"<code>/{command}</code>" in memory
    for command in ("ai_memory_off", "ai_memory_forget"):
        assert f"<code>/{command}</code> <i>(Only admins)</i>" in memory


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
    assert [button["text"] for row in buttons for button in row] == ["Back to list"]
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
    prompt = await test_client.send_command(command="ai_memory_forget", from_user=user, chat=group)
    await test_client.send_callback(_callback(prompt, "Clear shared memory"), from_user=user, message=message)
    requests = await test_client.send_callback(callback, from_user=user, message=message)
    assert "No memories match your search" in _message_text(requests)
    requests = await test_client.send_callback(fact_callback, from_user=user, message=message)
    assert "This memory is no longer in this list" in _message_text(requests)
