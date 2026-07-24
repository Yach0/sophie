from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan
from sophie_bot.modules.federations.middlewares import check_fban
from sophie_bot.modules.federations.middlewares.check_fban import FedBanMiddleware
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.modules.federations.services.common import normalize_chat_iids


async def build_chat_model(tid: int, title: str, chat_type: ChatType) -> ChatModel:
    chat_model = ChatModel(
        tid=tid,
        type=chat_type,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await chat_model.insert()
    reloaded_chat = await ChatModel.get_by_tid(tid)
    assert reloaded_chat is not None
    return reloaded_chat


def build_message(chat_tid: int, user_tid: int) -> SimpleNamespace:
    return SimpleNamespace(
        sender_chat=None,
        chat=SimpleNamespace(type="supergroup", id=chat_tid),
        from_user=SimpleNamespace(id=user_tid, first_name="Fbanned User"),
        reply=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_repeated_messages_from_fbanned_user_do_not_duplicate_banned_chats(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_init
    await ChatModel.delete_all()
    await Federation.delete_all()
    await FederationBan.delete_all()

    user_tid = 444444444
    chat_tid = -100444444444
    group_chat = await build_chat_model(chat_tid, "Fed Group", ChatType.supergroup)
    fed_creator = await build_chat_model(555555555, "Fed Creator", ChatType.private)
    fbanned_user = await build_chat_model(user_tid, "Fbanned User", ChatType.private)

    federation = Federation(fed_name="Test Fed", fed_id="test-fed-id", creator=fed_creator, chats=[group_chat])
    await federation.insert()

    ban = FederationBan(
        fed_id=federation.fed_id,
        user_id=user_tid,
        time=datetime.now(UTC),
        by=fed_creator,
        reason="Spam",
    )
    await ban.insert()

    # mongomock cannot match the `chats` DBRef predicate used by the real chat->federation lookup.
    async def resolve_federation(_chat_iid: Any) -> Federation:
        return federation

    monkeypatch.setattr(FederationManageService, "get_federation_for_chat", resolve_federation)
    monkeypatch.setattr(check_fban, "ban_user", AsyncMock(return_value=True))
    monkeypatch.setattr(check_fban, "is_user_admin", AsyncMock(return_value=False))

    middleware = FedBanMiddleware()
    data: dict[str, Any] = {"chat_db": group_chat, "user_db": fbanned_user}

    for _ in range(3):
        assert await middleware.is_fbanned(build_message(chat_tid, user_tid), data) is True

    reloaded_ban = await FederationBan.find_one(FederationBan.fed_id == federation.fed_id)
    assert reloaded_ban is not None
    banned_chat_iids = normalize_chat_iids([banned_chat.to_ref() for banned_chat in reloaded_ban.banned_chats])
    assert banned_chat_iids == [group_chat.iid]
