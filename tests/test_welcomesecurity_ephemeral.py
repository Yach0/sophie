from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

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
        patch(
            "sophie_bot.modules.greetings.middlewares.new_user.is_enabled", AsyncMock(return_value=ephemeral)
        ),
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
