from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import MessageEntity, RichBlockParagraph, RichMessage, RichTextCustomEmoji, Update
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models import ChatModel, FiltersModel, LocksModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.db.models.ai.ai_quota import AIQuotaModel
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.modules.ai.utils.ai_header import AI_CUSTOM_EMOJI_ID
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_quota_docs import build_chatbot_quota_exhausted_doc
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_message_id, set_feature
from tests.e2e.test_ai_reply_context_e2e import _history_capture


async def _exhaust_quota(chat: ChatModel, services: ApplicationServices) -> None:
    quota = await AIQuotaModel.get_or_create(chat)
    quota_info = await get_quota_info(chat.iid, redis=services.redis)
    assert quota_info is not None
    quota.used_credits = quota_info.total_credits
    await quota.save()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ("mention", "caption", "command", "reply", "wrong", "support", "disabled"))
async def test_group_bot_mention_routing(
    test_client: TestClient, test_services: ApplicationServices, route: str
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client, first_name="Alice")
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    mode = AIMode(route) if route in {"support", "disabled"} else AIMode.entertainment
    await AIModeModel.set_mode(chat, mode)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_memory_writer", False, chat_tid=group.id)

    username = (await test_client.bot.me()).username
    assert username
    mention = f"@{username.upper()}" if route != "wrong" else "@another_bot"
    text = f"{mention} help"
    parent = None
    if route == "command":
        text = f"/ai {text}"
    if route == "reply":
        # Use the existing recognized AI title from the reply-route regression tests.
        from_bot = (await test_client.bot.me()).model_copy(update={"first_name": "Sophie"})
        parent = MessageFactory.create(text="Answer", from_user=from_bot, chat=group)
        parent = parent.model_copy(
            update={
                "rich_message": RichMessage(
                    blocks=[
                        RichBlockParagraph(
                            text=[
                                RichTextCustomEmoji(custom_emoji_id=AI_CUSTOM_EMOJI_ID, alternative_text="✨"),
                                " Answer",
                            ]
                        )
                    ]
                )
            }
        )
    message = MessageFactory.create(
        text=text,
        from_user=member,
        chat=group,
        reply_to_message=parent,
        message_id=next_message_id(),
        # Telegram sends whole seconds; aiogram rounds fractional dates on dispatch,
        # which could otherwise put this message beyond the cache read's current time.
        date=datetime.now(UTC).replace(microsecond=0),
    ).model_copy(
        update={"entities": [MessageEntity(type="mention", offset=4 if route == "command" else 0, length=len(mention))]}
    )
    if route == "caption":
        message = message.model_copy(
            update={
                "text": None,
                "entities": None,
                "caption": text,
                "caption_entities": message.entities,
            }
        )
    prompts: list[list[Any]] = []
    calls: dict[str, AsyncMock] = {}
    with ExitStack() as stack:
        for module in ("mention", "ai_cmd", "reply"):
            calls[module] = AsyncMock(side_effect=_history_capture(prompts, test_services))
            stack.enter_context(
                patch(
                    f"sophie_bot.modules.ai.handlers.{module}.ai_chatbot_reply",
                    calls[module],
                )
            )
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))

    if route in {"wrong", "support", "disabled"}:
        assert prompts == []
    else:
        assert len(prompts) == 1
        expected_route = "ai_cmd" if route == "command" else "reply" if route == "reply" else "mention"
        assert calls[expected_route].await_count == 1
        if route in {"mention", "caption"}:
            assert f"{member.full_name}: help" in prompts[0]
        cached = await get_cached_messages(group.id, redis=test_services.redis)
        current_rows = [row for row in cached if row.message_id == message.message_id]
        assert len(current_rows) == 1
        current = current_rows[0]
        assert not current.eligible_for_proactive_ai


@pytest.mark.asyncio
@pytest.mark.parametrize("caption", (False, True))
@pytest.mark.parametrize("is_admin", (False, True))
async def test_disabled_ai_command_does_not_fall_back_to_mention(
    test_client: TestClient, test_services: ApplicationServices, caption: bool, is_admin: bool
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    await DisablingModel.disable(chat.iid, "ai")
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_memory_writer", False, chat_tid=group.id)
    if is_admin:
        await grant_admin(group.id, member.id)

    username = (await test_client.bot.me()).username
    assert username
    text = f"/ai @{username} help"
    entities = [
        MessageEntity(type="bot_command", offset=0, length=3),
        MessageEntity(type="mention", offset=4, length=len(username) + 1),
    ]
    message = MessageFactory.create(text=text, from_user=member, chat=group, message_id=next_message_id()).model_copy(
        update={
            "text": None if caption else text,
            "entities": None if caption else entities,
            "caption": text if caption else None,
            "caption_entities": entities if caption else None,
        }
    )
    prompts: list[list[Any]] = []
    command_reply = AsyncMock(side_effect=_history_capture(prompts, test_services))
    mention_reply = AsyncMock(side_effect=_history_capture(prompts, test_services))
    with (
        patch("sophie_bot.modules.ai.handlers.ai_cmd.ai_chatbot_reply", command_reply),
        patch("sophie_bot.modules.ai.handlers.mention.ai_chatbot_reply", mention_reply),
    ):
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))

    mention_reply.assert_not_awaited()
    if is_admin:
        command_reply.assert_awaited_once()
        assert command_reply.call_args.args[2] == f"@{username} help"
    else:
        command_reply.assert_not_awaited()
        assert prompts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enforcement", ("filter", "lock", "antiflood"))
@pytest.mark.parametrize("caption", (False, True))
@pytest.mark.parametrize("exhausted_quota", (False, True))
async def test_bot_mention_preserves_moderation(
    test_client: TestClient,
    test_services: ApplicationServices,
    enforcement: str,
    caption: bool,
    exhausted_quota: bool,
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    if exhausted_quota:
        await _exhaust_quota(chat, test_services)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_memory_writer", False, chat_tid=group.id)
    await grant_bot_admin(group.id)
    if enforcement == "filter":
        await set_feature(test_client, "filters", True, chat_tid=group.id)
        await FiltersModel(chat=chat.iid, handler="spam", action=None, actions={"delmsg": None}).insert()
    elif enforcement == "lock":
        await set_feature(test_client, "locks", True, chat_tid=group.id)
        await LocksModel(chat=chat.iid, locked_types={"cyrillic"}).insert()
    else:
        await set_feature(test_client, "antiflood", True, chat_tid=group.id)
        await AntifloodModel(chat=chat.iid, enabled=True, message_count=2).insert()
        await test_client.send_message(text="first", from_user=member, chat=group)

    username = (await test_client.bot.me()).username
    assert username
    text = f"@{username} spam Привет"
    entities = [MessageEntity(type="mention", offset=0, length=len(username) + 1)]
    message = MessageFactory.create(text=text, from_user=member, chat=group, message_id=next_message_id()).model_copy(
        update={
            "text": None if caption else text,
            "entities": None if caption else entities,
            "caption": text if caption else None,
            "caption_entities": entities if caption else None,
        }
    )
    test_client.capture.clear()
    with patch("sophie_bot.modules.ai.handlers.mention.ai_chatbot_reply", new_callable=AsyncMock) as ai_reply:
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))
    ai_reply.assert_not_awaited()
    deleted = test_client.capture.get_by_type(RequestType.DELETE_MESSAGE)
    assert len(deleted) == 1
    assert deleted[0].params["message_id"] == message.message_id
    notices = test_client.capture.get_by_type(RequestType.SEND_MESSAGE)
    assert all("AI Quota Exhausted" not in notice.params["text"] for notice in notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("caption", (False, True))
async def test_exhausted_bot_mention_notifies_without_repeating_moderation(
    test_client: TestClient, test_services: ApplicationServices, caption: bool
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_memory_writer", False, chat_tid=group.id)
    await set_feature(test_client, "antiflood", True, chat_tid=group.id)
    await grant_bot_admin(group.id)
    await AntifloodModel(chat=chat.iid, enabled=True, message_count=3).insert()
    await _exhaust_quota(chat, test_services)
    quota_info = await get_quota_info(chat.iid, redis=test_services.redis)
    assert quota_info is not None
    expected_notice = str(build_chatbot_quota_exhausted_doc(quota_info.total_credits, quota_info.period_end))
    username = (await test_client.bot.me()).username
    assert username
    text = f"@{username} help"
    entities = [MessageEntity(type="mention", offset=0, length=len(username) + 1)]

    with patch("sophie_bot.modules.ai.handlers.mention.ai_chatbot_reply", new_callable=AsyncMock) as ai_reply:
        for _attempt in range(2):
            message = MessageFactory.create(
                text=text, from_user=member, chat=group, message_id=next_message_id()
            ).model_copy(
                update={
                    "text": None if caption else text,
                    "entities": None if caption else entities,
                    "caption": text if caption else None,
                    "caption_entities": entities if caption else None,
                }
            )
            test_client.capture.clear()
            await test_client.dispatcher.feed_update(
                test_client.bot, Update(update_id=next_message_id(), message=message)
            )
            notices = test_client.capture.get_by_type(RequestType.SEND_MESSAGE)
            assert len(notices) == 1
            assert notices[0].params["text"] == expected_notice
            assert notices[0].params["reply_parameters"]["message_id"] == message.message_id
            assert not test_client.capture.get_by_type(RequestType.DELETE_MESSAGE)
            assert not test_client.capture.get_by_type(RequestType.RESTRICT_CHAT_MEMBER)
        ai_reply.assert_not_awaited()

    # Two mentions must count as two messages; duplicate enforcement would flood
    # on the second mention. The next ordinary message must hit the real limit.
    test_client.capture.clear()
    await test_client.send_message(text="third", from_user=member, chat=group)
    assert len(test_client.capture.get_by_type(RequestType.DELETE_MESSAGE)) == 1
    assert len(test_client.capture.get_by_type(RequestType.RESTRICT_CHAT_MEMBER)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("caption", (False, True))
@pytest.mark.parametrize("prefix", ("/", "!"))
async def test_later_filter_command_with_bot_mention_keeps_its_route(
    test_client: TestClient, caption: bool, prefix: str
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, member.id)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_memory_writer", False, chat_tid=group.id)
    await set_feature(test_client, "filters", True, chat_tid=group.id)
    username = (await test_client.bot.me()).username
    assert username
    text = f"{prefix}add_filter @{username}"
    entities = [MessageEntity(type="mention", offset=12, length=len(username) + 1)]
    message = MessageFactory.create(text=text, from_user=member, chat=group, message_id=next_message_id()).model_copy(
        update={
            "text": None if caption else text,
            "entities": None if caption else entities,
            "caption": text if caption else None,
            "caption_entities": entities if caption else None,
        }
    )
    with patch("sophie_bot.modules.ai.handlers.mention.ai_chatbot_reply", new_callable=AsyncMock) as ai_reply:
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))
    ai_reply.assert_not_awaited()
    # add_filter accepts text commands only; captions must retain that behavior.
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=group.id, user_id=member.id)
    wizard = (await state.get_data()).get("wizard")
    if caption:
        assert wizard is None
    else:
        assert wizard is not None
        assert wizard["draft"]["handler"] == f"@{username}"
