from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message
from stfu_tg import Bold, Doc

from sophie_bot.constants import FILTERS_SILENT_MODE_DELETE_DELAY_SECONDS
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from sophie_bot.modules.filters.filter_wizard import FilterDraft


def _message_stub(message_id: int) -> Message:
    message = AsyncMock(spec=Message)
    message.message_id = message_id
    return message


async def _run_process_filters(
    monkeypatch: pytest.MonkeyPatch,
    *,
    silent: bool,
    action_messages: list,
    reply: Message | None,
    flag_enabled: bool = True,
) -> tuple[MagicMock, Message]:
    """Drives EnforceFiltersMiddleware._process_filters with a single matching filter."""
    middleware = EnforceFiltersMiddleware()

    message = _message_stub(111)
    message.chat = SimpleNamespace(id=-100125)
    message.reply = AsyncMock(return_value=reply)

    filters = [SimpleNamespace(handler="exact:spam", effective_version=1, silent=silent)]

    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.FiltersModel.get_filters",
        AsyncMock(return_value=filters),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.match_filter_handler",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        middleware,
        "_process_filter",
        AsyncMock(return_value=([], action_messages)),
    )

    schedule_mock = MagicMock()
    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.schedule_message_deletion",
        schedule_mock,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.is_enabled",
        AsyncMock(return_value=flag_enabled),
    )

    with pytest.raises(SkipHandler):
        await middleware._process_filters(message, {"chat_db": SimpleNamespace(iid="chat-iid"), "user_in_group": None})

    return schedule_mock, message


@pytest.mark.asyncio
async def test_silent_filter_schedules_deletion_of_trigger_and_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_mock, _ = await _run_process_filters(
        monkeypatch, silent=True, action_messages=["spam detected"], reply=_message_stub(555)
    )

    schedule_mock.assert_called_once_with(
        -100125,
        [111, 555],
        delay_seconds=FILTERS_SILENT_MODE_DELETE_DELAY_SECONDS,
    )


@pytest.mark.asyncio
async def test_non_silent_filter_does_not_schedule_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_mock, _ = await _run_process_filters(
        monkeypatch, silent=False, action_messages=["spam detected"], reply=_message_stub(555)
    )

    schedule_mock.assert_not_called()


@pytest.mark.asyncio
async def test_silent_filter_does_nothing_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_mock, _ = await _run_process_filters(
        monkeypatch,
        silent=True,
        action_messages=["spam detected"],
        reply=_message_stub(555),
        flag_enabled=False,
    )

    schedule_mock.assert_not_called()


@pytest.mark.asyncio
async def test_silent_filter_deletes_trigger_when_action_sends_its_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actions carrying buttons/files send their own message and return it instead of text."""
    self_sent = _message_stub(777)

    schedule_mock, message = await _run_process_filters(
        monkeypatch, silent=True, action_messages=[self_sent], reply=None
    )

    # No aggregated text remained, so no extra reply should have been sent
    message.reply.assert_not_awaited()
    schedule_mock.assert_called_once_with(
        -100125,
        [111, 777],
        delay_seconds=FILTERS_SILENT_MODE_DELETE_DELAY_SECONDS,
    )


@pytest.mark.asyncio
async def test_action_returning_several_messages_contributes_every_id() -> None:
    """Album notes and the rules action send more than one message; all must be cleaned up."""
    middleware = EnforceFiltersMiddleware()

    message = _message_stub(111)
    message.chat = SimpleNamespace(id=-100125)
    message.reply = AsyncMock(return_value=_message_stub(555))

    album = [_message_stub(801), _message_stub(802), _message_stub(803)]
    sent_ids = await middleware._handle_action_messages(message, [album])

    assert sent_ids == [801, 802, 803]
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_stfu_elements_are_never_mistaken_for_sent_messages() -> None:
    """stfu Doc/Bold subclass list, so they must still be rendered as text."""
    middleware = EnforceFiltersMiddleware()

    message = _message_stub(111)
    message.chat = SimpleNamespace(id=-100125)
    message.reply = AsyncMock(return_value=_message_stub(555))

    sent_ids = await middleware._handle_action_messages(message, [Doc(Bold("important"))])

    assert sent_ids == [555]
    assert "important" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_self_sent_message_is_not_rendered_into_reply_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Message must never be stringified into the aggregated doc."""
    middleware = EnforceFiltersMiddleware()

    message = _message_stub(111)
    message.chat = SimpleNamespace(id=-100125)
    message.reply = AsyncMock(return_value=_message_stub(555))

    sent_ids = await middleware._handle_action_messages(message, [_message_stub(777), "visible text"])

    assert sent_ids == [777, 555]
    sent_text = message.reply.await_args.args[0]
    assert "visible text" in sent_text
    assert "Message" not in sent_text


def test_silent_survives_setup_round_trip() -> None:
    filter_draft = FilterDraft(handler="spam", actions={}, silent=True)
    restored = FilterDraft.model_validate(filter_draft.model_dump(mode="json"))
    assert restored.silent is True
