from types import SimpleNamespace

import pytest
from aiogram.enums import ChatType
from beanie import PydanticObjectId

from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.scheduler import chat_language, for_chats
from sophie_bot.modules.utils_.scheduler.chat_language import UseChatLanguage
from sophie_bot.modules.utils_.scheduler.for_chats import ForChats


def test_is_real_reply_requires_reply_message() -> None:
    message = SimpleNamespace(reply_to_message=None)

    assert is_real_reply(message) is False


def test_is_real_reply_rejects_forum_topic_creation_service_reply() -> None:
    reply = SimpleNamespace(forum_topic_created=SimpleNamespace(name="topic"))
    message = SimpleNamespace(reply_to_message=reply)

    assert is_real_reply(message) is False


def test_is_real_reply_accepts_regular_reply() -> None:
    reply = SimpleNamespace(forum_topic_created=None)
    message = SimpleNamespace(reply_to_message=reply)

    assert is_real_reply(message) is True


class FakeContextLocale:
    def __init__(self) -> None:
        self.set_values: list[str] = []
        self.reset_tokens: list[str] = []

    def set(self, locale: str) -> str:
        self.set_values.append(locale)
        return f"ctx-token:{locale}"

    def reset(self, token: str) -> None:
        self.reset_tokens.append(token)


class FakeI18n:
    def __init__(self) -> None:
        self.ctx_locale = FakeContextLocale()
        self.current_values: list[object] = []
        self.reset_tokens: list[str] = []

    def set_current(self, value: object) -> str:
        self.current_values.append(value)
        return "current-token"

    def reset_current(self, token: str) -> None:
        self.reset_tokens.append(token)


@pytest.mark.asyncio
async def test_use_chat_language_sets_and_resets_i18n_context(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    fake_i18n = FakeI18n()

    async def fake_get_locale(requested_chat_iid: PydanticObjectId) -> str:
        assert requested_chat_iid == chat_iid
        return "uk"

    monkeypatch.setattr(chat_language.LanguageModel, "get_locale", fake_get_locale)
    monkeypatch.setattr(chat_language, "i18n", fake_i18n)

    async with UseChatLanguage(chat_iid) as context:
        assert context.chat_iid == chat_iid
        assert fake_i18n.ctx_locale.set_values == ["uk"]
        assert fake_i18n.current_values == [fake_i18n]

    assert fake_i18n.ctx_locale.reset_tokens == ["ctx-token:uk"]
    assert fake_i18n.reset_tokens == ["current-token"]


class FakeChatModel:
    type = "chat_type"
    find_calls: list[tuple[object]] = []

    @classmethod
    def find(cls, condition: object) -> "FakeChatQuery":
        cls.find_calls.append((condition,))
        return FakeChatQuery()


class FakeChatQuery:
    def __aiter__(self) -> str:
        return "chat-iterator"


def test_for_chats_builds_chat_type_query(monkeypatch: pytest.MonkeyPatch) -> None:
    conditions: list[tuple[object, tuple[ChatType, ...]]] = []

    def fake_in(field: object, chat_types: tuple[ChatType, ...]) -> str:
        conditions.append((field, chat_types))
        return "condition"

    FakeChatModel.find_calls = []
    monkeypatch.setattr(for_chats, "ChatModel", FakeChatModel)
    monkeypatch.setattr(for_chats, "In", fake_in)

    iterator = ForChats((ChatType.PRIVATE,)).__aiter__()

    assert iterator == "chat-iterator"
    assert conditions == [("chat_type", (ChatType.PRIVATE,))]
    assert FakeChatModel.find_calls == [("condition",)]
