from __future__ import annotations

from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.utils.telegram import is_bot_authored_message


def test_bot_authored_message_is_detected() -> None:
    bot_user = UserFactory.create(user_id=1001, first_name="Relay Bot", is_bot=True)
    message = MessageFactory.create(text="hello", from_user=bot_user)

    assert is_bot_authored_message(message) is True


def test_human_message_is_not_bot_authored() -> None:
    human = UserFactory.create(user_id=1002, first_name="Human", is_bot=False)
    message = MessageFactory.create(text="hello", from_user=human)

    assert is_bot_authored_message(message) is False


def test_sender_chat_takes_precedence_over_anonymous_bot_identity() -> None:
    anonymous_bot = UserFactory.create(user_id=1087968824, first_name="GroupAnonymousBot", is_bot=True)
    sender_chat = ChatFactory.create_group(chat_id=-1002000000001, title="Anonymous Admin Group")
    message = MessageFactory.create(text="hello", from_user=anonymous_bot).model_copy(
        update={"sender_chat": sender_chat}
    )

    assert is_bot_authored_message(message) is False
