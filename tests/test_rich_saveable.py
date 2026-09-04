from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import types
from aiogram.methods import SendRichMessage

from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.notes.api.schemas import NoteCreate
from sophie_bot.modules.notes.utils import parse as parse_module
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.notes.utils.rich import rich_message_to_html_fallback, rich_message_to_input


def _message_with_rich(rich_message: types.RichMessage) -> types.Message:
    return types.Message(
        message_id=1,
        date=0,
        chat=types.Chat(id=-100, type="group", title="Group"),
        from_user=types.User(id=10, is_bot=False, first_name="User"),
        rich_message=rich_message,
    )


def test_rich_fallback_and_input_preserve_visible_structure() -> None:
    rich_message = types.RichMessage(
        blocks=[
            types.RichBlockParagraph(text=types.RichTextBold(text="<safe>")),
            types.RichBlockPhoto(
                photo=[
                    types.PhotoSize(file_id="small", file_unique_id="s", width=1, height=1, file_size=100),
                    types.PhotoSize(file_id="large", file_unique_id="l", width=10, height=10, file_size=1),
                ]
            ),
        ]
    )

    assert rich_message_to_html_fallback(rich_message) == "<b>&lt;safe&gt;</b>\n[Photo]"
    converted = rich_message_to_input(rich_message)
    assert converted.blocks[1].photo.media == "large"
    assert (
        Saveable(text=rich_message_to_html_fallback(rich_message), rich_message=rich_message, version=3).model_dump(
            mode="json"
        )["rich_message"]["blocks"][0]["type"]
        == "paragraph"
    )


@pytest.mark.asyncio
async def test_parse_rich_message_derives_fallback_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    rich_message = types.RichMessage(blocks=[types.RichBlockParagraph(text="Hello")])
    message = _message_with_rich(rich_message)
    monkeypatch.setattr(parse_module, "is_enabled", AsyncMock(return_value=True))

    saveable = await parse_module.parse_saveable(message, None, owner_chat_tid=-100)

    assert saveable.version == 3
    assert saveable.text == "Hello"
    assert saveable.file is None
    assert saveable.files == []


def test_api_rejects_bot_bound_rich_buttons() -> None:
    rich_message = types.RichMessage(
        blocks=[
            types.RichBlockButtons(
                buttons=[types.RichMessageButton(text="Run", callback_data="danger")],
            )
        ]
    )
    with pytest.raises(ValueError, match="Bot-bound"):
        NoteCreate(names=("rich",), rich_message=rich_message)


@pytest.mark.asyncio
async def test_send_rich_message_uses_typed_method_and_raw_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[object] = []

    def fake_emit(method: object, _bot: object) -> object:
        emitted.append(method)

        async def result() -> object:
            return SimpleNamespace(message_id=2)

        return result()

    monkeypatch.setattr("aiogram.methods.base.TelegramMethod.emit", fake_emit)
    monkeypatch.setattr(send_module, "is_enabled", AsyncMock(return_value=True))
    rich_message = types.RichMessage(
        blocks=[
            types.RichBlockParagraph(text="Content"),
            types.RichBlockButtons(buttons=[types.RichMessageButton(text="Action", url="https://example.test")]),
        ]
    )

    await send_module.send_saveable(
        _message_with_rich(rich_message),
        -100,
        Saveable(text="Content", rich_message=rich_message, version=3),
        raw=True,
        owner_chat_tid=-100,
    )

    assert isinstance(emitted[0], SendRichMessage)
    assert emitted[0].rich_message.blocks[1].text == "Action"
    assert rich_message.blocks[1].buttons[0].url == "https://example.test"
