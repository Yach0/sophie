from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from datetime import datetime, timezone

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message

from sophie_bot.modules.greetings.middlewares.new_user import NewUserMiddleware


def _chat(tid: int, iid: str) -> SimpleNamespace:
    return SimpleNamespace(tid=tid, iid=iid, is_bot=False)


async def _run_on_captcha(ephemeral: bool, muted: list[bool], new_users: list[SimpleNamespace]):
    chat_db = _chat(-100123, "chat-iid")
    message = SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=1, message_thread_id=None, from_user=None)

    with (
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.ws_on_new_users_mute",
            AsyncMock(return_value=muted),
        ),
        patch("sophie_bot.modules.greetings.middlewares.new_user.is_enabled", AsyncMock(return_value=ephemeral)),
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.send_welcome",
            AsyncMock(return_value=SimpleNamespace(message_id=42)),
        ) as send_welcome,
        patch("sophie_bot.modules.greetings.middlewares.new_user.aredis.set", AsyncMock()) as redis_set,
    ):
        await NewUserMiddleware.on_captcha(
            message,
            SimpleNamespace(security_note=None),
            chat_db,
            new_users,
            SimpleNamespace(id=1),
            cleanservice_enabled=False,
            chat_rules=None,
        )

    return send_welcome, redis_set


@pytest.mark.asyncio
async def test_ephemeral_prompt_goes_to_every_new_member() -> None:
    users = [_chat(1, "u1"), _chat(2, "u2")]

    send_welcome, redis_set = await _run_on_captcha(ephemeral=True, muted=[True, True], new_users=users)

    receivers = [call.kwargs["receiver_user_id"] for call in send_welcome.await_args_list]
    assert receivers == [1, 2]
    # Nothing is left in the chat, so nothing is recorded for the cleanup that deletes it later.
    redis_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_ephemeral_prompt_skips_members_that_were_not_muted() -> None:
    users = [_chat(1, "u1"), _chat(2, "u2")]

    send_welcome, _ = await _run_on_captcha(ephemeral=True, muted=[False, True], new_users=users)

    assert [call.kwargs["receiver_user_id"] for call in send_welcome.await_args_list] == [2]


@pytest.mark.asyncio
async def test_without_the_flag_one_prompt_is_sent_to_the_chat_and_tracked() -> None:
    users = [_chat(1, "u1")]

    send_welcome, redis_set = await _run_on_captcha(ephemeral=False, muted=[True], new_users=users)

    assert send_welcome.await_args.kwargs["receiver_user_id"] is None
    redis_set.assert_awaited_once()


def _member(user_id: int, is_bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, is_bot=is_bot)


async def _run_welcome(ephemeral: bool, members: list[SimpleNamespace]):
    chat_db = _chat(-100123, "chat-iid")
    # The middleware only acts on a real Message, so construct one without validating it.
    event = Message.model_construct(
        chat=SimpleNamespace(id=-100123, join_by_request=False),
        message_id=1,
        message_thread_id=None,
        from_user=SimpleNamespace(id=999),
        new_chat_members=members,
        date=datetime.now(timezone.utc),
    )
    greetings = SimpleNamespace(
        note=None,
        welcome_disabled=False,
        welcome_security=None,
        welcome_mute=None,
        clean_service=None,
        clean_welcome=None,
    )
    new_users = [_chat(member.id, f"u{member.id}") for member in members]
    for chat_model, member in zip(new_users, members):
        chat_model.is_bot = member.is_bot

    async def flag(feature: str, chat_tid: int | None = None) -> bool:
        return ephemeral if feature == "greetings_ephemeral" else False

    with (
        patch("sophie_bot.modules.greetings.middlewares.new_user.is_enabled", AsyncMock(side_effect=flag)),
        patch("sophie_bot.modules.greetings.middlewares.new_user.is_user_admin", AsyncMock(return_value=False)),
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.GreetingsModel.get_by_chat_iid",
            AsyncMock(return_value=greetings),
        ),
        patch("sophie_bot.modules.greetings.middlewares.new_user.RulesModel.get_rules", AsyncMock(return_value=None)),
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.NewUserMiddleware.is_join_request",
            AsyncMock(return_value=False),
        ),
        patch("sophie_bot.modules.greetings.middlewares.new_user.NewUserMiddleware.cleanup", AsyncMock()) as cleanup,
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.send_welcome",
            AsyncMock(return_value=SimpleNamespace(message_id=42)),
        ) as send_welcome,
    ):
        with pytest.raises(SkipHandler):
            await NewUserMiddleware()(AsyncMock(), event, {"chat_db": chat_db, "new_users": new_users})

    return send_welcome, cleanup


@pytest.mark.asyncio
async def test_ephemeral_welcome_greets_every_human_member_separately() -> None:
    send_welcome, cleanup = await _run_welcome(
        ephemeral=True, members=[_member(1), _member(2), _member(3, is_bot=True)]
    )

    calls = send_welcome.await_args_list
    assert [call.kwargs["receiver_user_id"] for call in calls] == [1, 2]
    # Each greeting is filled for the member who receives it, not for whoever joined first.
    assert [call.kwargs["user"].id for call in calls] == [1, 2]
    # Nothing is in the chat, so the clean-welcome cleanup is given no message to track.
    assert cleanup.await_args.args[2] is None


@pytest.mark.asyncio
async def test_without_the_flag_one_welcome_goes_to_the_chat() -> None:
    send_welcome, cleanup = await _run_welcome(ephemeral=False, members=[_member(1), _member(2)])

    assert send_welcome.await_count == 1
    assert "receiver_user_id" not in send_welcome.await_args.kwargs
    assert cleanup.await_args.args[2] is not None
