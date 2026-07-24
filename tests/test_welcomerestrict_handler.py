from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from sophie_bot.db.models import ChatModel, GreetingsModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.welcomesecurity.handlers.enable_welcomemute import EnableWelcomeMute


@pytest.fixture
async def greetings(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real GreetingsModel for a real chat.

    ``get_by_chat_iid`` is patched because its ``GreetingsModel.chat.id`` predicate
    resolves to a DBRef path that mongomock cannot match; everything below it —
    ``WelcomeMute`` validation and ``set_status_welcomemute`` — stays real.
    """
    chat = ChatModel(
        tid=-100987654,
        type=ChatType.supergroup,
        first_name_or_title="Restrict Group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await chat.insert()
    chat = await ChatModel.get_by_tid(chat.tid)

    model = GreetingsModel(chat=chat)
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.enable_welcomemute.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=model),
    )

    yield model

    await model.delete()
    await chat.delete()


def _handler(greetings_model: GreetingsModel, i18n: Any, **data: Any) -> EnableWelcomeMute:
    event = MagicMock(spec=Message)
    event.reply = AsyncMock()

    chat: ChatModel = greetings_model.chat  # type: ignore[assignment]
    return EnableWelcomeMute(
        event,
        connection=ChatConnection(
            type=ChatType.supergroup,
            is_connected=False,
            tid=chat.tid,
            title=chat.first_name_or_title,
            db_model=chat,
        ),
        i18n=i18n,
        **data,
    )


@pytest.mark.asyncio
async def test_welcomerestrict_on_enables_with_default_time(greetings: GreetingsModel, i18n_context: Any) -> None:
    """Regression: '/welcomerestrict on' parses to True and must enable with the default duration."""
    await _handler(greetings, i18n_context, new_status=True).handle()

    assert greetings.welcome_mute is not None
    assert greetings.welcome_mute.enabled is True
    assert greetings.welcome_mute.time == WELCOMEMUTE_DEFAULT_TIME


@pytest.mark.asyncio
async def test_welcomerestrict_duration_enables_with_that_time(greetings: GreetingsModel, i18n_context: Any) -> None:
    """'/welcomerestrict 12h' enables the restriction for the given duration."""
    await _handler(greetings, i18n_context, new_status=timedelta(hours=12)).handle()

    assert greetings.welcome_mute is not None
    assert greetings.welcome_mute.enabled is True
    assert greetings.welcome_mute.time == timedelta(hours=12)


@pytest.mark.asyncio
async def test_welcomerestrict_off_disables_and_keeps_time(greetings: GreetingsModel, i18n_context: Any) -> None:
    """'/welcomerestrict off' disables the restriction without dropping the stored duration."""
    await _handler(greetings, i18n_context, new_status=timedelta(hours=12)).handle()
    await _handler(greetings, i18n_context, new_status=False).handle()

    assert greetings.welcome_mute is not None
    assert greetings.welcome_mute.enabled is False
    assert greetings.welcome_mute.time == timedelta(hours=12)


@pytest.mark.asyncio
async def test_welcomerestrict_without_args_shows_status(greetings: GreetingsModel, i18n_context: Any) -> None:
    """'/welcomerestrict' with no argument renders the current state instead of changing it."""
    greetings.welcome_mute.enabled = True
    greetings.welcome_mute.time = timedelta(hours=12)

    handler = _handler(greetings, i18n_context)
    await handler.handle()

    handler.event.reply.assert_awaited_once()
    assert "12 hours" in handler.event.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_welcomerestrict_status_text_covers_the_whole_status_domain(
    greetings: GreetingsModel, i18n_context: Any
) -> None:
    """Every value get_status can return must render; no ValueError escape hatch."""
    handler = _handler(greetings, i18n_context)

    assert str(handler.status_text(False)) == "Disabled"
    assert "12 hours" in str(handler.status_text(timedelta(hours=12)))
