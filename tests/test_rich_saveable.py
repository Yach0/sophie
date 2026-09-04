from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import types
from aiogram.methods import SendRichMessage
from beanie import PydanticObjectId

from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.notes.api import update as update_module
from sophie_bot.modules.notes.api.schemas import NoteCreate, NoteUpdate
from sophie_bot.modules.notes.utils import parse as parse_module
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.notes.utils.rich import (
    is_trusted_rich_source,
    rich_message_to_html_fallback,
    rich_message_to_input,
    validate_rich_message_structure,
)


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


def test_rich_input_conversion_preserves_nested_text_table_cells_and_video_metadata() -> None:
    rich_message = types.RichMessage(
        blocks=[
            types.RichBlockParagraph(text=types.RichTextBold(text="nested")),
            types.RichBlockTable(
                cells=[[types.RichBlockTableCell(align="left", valign="top", text=None)]],
            ),
            types.RichBlockVideo(
                video=types.Video(
                    file_id="video",
                    file_unique_id="video-unique",
                    width=10,
                    height=10,
                    duration=2,
                    cover=[
                        types.PhotoSize(
                            file_id="cover-small",
                            file_unique_id="cover-small-unique",
                            width=1,
                            height=1,
                        ),
                        types.PhotoSize(
                            file_id="cover-large",
                            file_unique_id="cover-large-unique",
                            width=10,
                            height=10,
                        ),
                    ],
                    start_timestamp=4,
                )
            ),
        ]
    )

    converted = rich_message_to_input(rich_message)

    assert converted.blocks[0].text.text == "nested"
    assert converted.blocks[1].cells[0][0].text is None
    assert converted.blocks[2].video.cover == "cover-large"
    assert converted.blocks[2].video.start_timestamp.second == 4


def test_rich_validation_rejects_invalid_button_rows_and_labels() -> None:
    too_many_buttons = types.RichMessage(
        blocks=[
            types.RichBlockButtons(
                buttons=[types.RichMessageButton(text="button", url="https://example.test")] * 9,
            )
        ]
    )
    with pytest.raises(ValueError, match="between 1 and 8"):
        validate_rich_message_structure(too_many_buttons)

    invalid_label = types.RichMessage(
        blocks=[
            types.RichBlockButtons(
                buttons=[
                    types.RichMessageButton(
                        text=types.RichTextBold(text="formatted"),
                        url="https://example.test",
                    )
                ],
            )
        ]
    )
    with pytest.raises(ValueError, match="only plain text"):
        validate_rich_message_structure(invalid_label)


def test_rich_source_trust_accepts_via_bot_identity() -> None:
    source_message = SimpleNamespace(via_bot=SimpleNamespace(id=42))

    assert is_trusted_rich_source(source_message, 42)


def test_note_create_rejects_two_legacy_media_representations() -> None:
    file_data = {"id": "file", "type": "document"}

    with pytest.raises(ValueError, match="file and files"):
        NoteCreate(names=("rich",), file=file_data, files=[file_data])


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
async def test_note_update_keeps_rich_message_typed_and_clears_legacy_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note_id = PydanticObjectId()
    note = SimpleNamespace(id=note_id, chat_tid=-100, rich_message=None, names=("rich",), save=AsyncMock())
    rich_message = types.RichMessage(blocks=[types.RichBlockParagraph(text="Updated")])
    monkeypatch.setattr(update_module.NoteModel, "get", AsyncMock(return_value=note))
    monkeypatch.setattr(update_module, "log_event", AsyncMock())
    monkeypatch.setattr(
        update_module.NoteResponse,
        "from_model",
        classmethod(lambda _cls, model: model),
    )

    result = await update_module.update_note(
        SimpleNamespace(tid=-100, iid=PydanticObjectId()),
        note_id,
        NoteUpdate(rich_message=rich_message),
        SimpleNamespace(tid=7),
    )

    assert result is note
    assert note.rich_message is rich_message
    assert note.file is None
    assert note.files == []


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
