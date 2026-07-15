"""Tests for the `/warns` PM handler.

`WarnModel.user` is stored as a DBRef, which mongomock cannot traverse, so the query is
served from canned results. Those results are documents re-read from MongoDB, meaning the
handler sees the unfetched `Link` values it receives in production rather than the model
instances that `WarnModel(...)` keeps in memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.warns import WarnModel
from sophie_bot.modules.warns.handlers.warns_pm import WarnsPMHandler


async def _create_chat(chat_tid: int, title: str) -> ChatModel:
    chat = ChatModel(
        tid=chat_tid,
        type=ChatType.group,
        first_name_or_title=title,
        last_name=None,
        username=None,
        language_code=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat.save()
    return chat


async def _create_warn(chat: ChatModel, user: ChatModel, reason: str, date: datetime) -> WarnModel:
    warn = WarnModel(chat=chat, user=user, reason=reason, date=date)
    await warn.insert()
    stored = await WarnModel.get(warn.id)
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_warns_pm_lists_warns_with_chat_titles(db_init: object) -> None:
    user = await _create_chat(500001, "Warned user")
    first_chat = await _create_chat(-100501, "First group")
    second_chat = await _create_chat(-100502, "Second group")

    now = datetime.now(timezone.utc)
    warns = [
        await _create_warn(first_chat, user, "spamming", now - timedelta(days=1)),
        await _create_warn(second_chat, user, "flooding", now),
    ]

    message = SimpleNamespace(reply=AsyncMock())
    real_find = WarnModel.find

    def _find(*args: Any, **kwargs: Any) -> Any:
        query = real_find(*args, **kwargs)
        query.to_list = AsyncMock(return_value=warns)
        return query

    with patch.object(WarnModel, "find", _find):
        handler = WarnsPMHandler(message, user_db=user)
        await handler.handle()

    reply_text = message.reply.await_args.args[0]
    assert "First group" in reply_text
    assert "spamming" in reply_text
    assert "Second group" in reply_text
    assert "flooding" in reply_text


@pytest.mark.asyncio
async def test_warns_pm_reports_no_warns(db_init: object) -> None:
    user = await _create_chat(500002, "Clean user")
    message = SimpleNamespace(reply=AsyncMock())
    real_find = WarnModel.find

    def _find(*args: Any, **kwargs: Any) -> Any:
        query = real_find(*args, **kwargs)
        query.to_list = AsyncMock(return_value=[])
        return query

    with patch.object(WarnModel, "find", _find):
        handler = WarnsPMHandler(message, user_db=user)
        await handler.handle()

    assert "don't have warnings" in message.reply.await_args.args[0]
