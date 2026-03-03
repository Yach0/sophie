from __future__ import annotations

from typing import Optional

from aiogram.enums import ContentType
from aiogram.enums.message_entity_type import MessageEntityType
from aiogram.types import Message, MessageEntity
from stfu_tg import Section, Template

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, NoteFile, Saveable, SaveableEntity
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList, parse_message_buttons
from sophie_bot.modules.notes.utils.buttons_processor.list_from_message import parse_buttons_list_from_message
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _

PARSABLE_CONTENT_TYPES: tuple[ContentType, ...] = (
    ContentType.AUDIO,
    ContentType.ANIMATION,
    ContentType.DOCUMENT,
    ContentType.PHOTO,  # LIST??
    ContentType.STICKER,
    ContentType.VIDEO,
    ContentType.VIDEO_NOTE,
    ContentType.VOICE,
    # ContentType.CONTACT,
    # ContentType.LOCATION,
    # ContentType.POLL,
    # ContentType.DICE
)
CONTENT_TYPES_WITH_FILE_ID: tuple[ContentType, ...] = (
    ContentType.AUDIO,
    ContentType.ANIMATION,
    ContentType.DOCUMENT,
    ContentType.PHOTO,
    ContentType.STICKER,
    ContentType.VIDEO,
    ContentType.VIDEO_NOTE,
    ContentType.VOICE,
)

SUPPORTS_CAPTION: tuple[ContentType, ...] = (
    ContentType.AUDIO,
    ContentType.ANIMATION,
    ContentType.DOCUMENT,
    ContentType.PHOTO,
)


def extract_file_info(message: Message) -> Optional[NoteFile]:
    if message.content_type not in PARSABLE_CONTENT_TYPES:
        return None

    # Get file ID from the parsable fields
    attr = getattr(message, message.content_type, None)

    if not attr:
        return None

    # Photos are lists
    if isinstance(attr, list):
        attr = attr[-1]

    file_id: Optional[str] = getattr(attr, "file_id", None)
    return NoteFile(id=file_id, type=ContentType(message.content_type)) if file_id else None


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _extract_custom_emoji_entities(
    entities: list[MessageEntity] | None, text: str | None, offset: int = 0
) -> list[SaveableEntity]:
    if not entities or not text:
        return []

    text_utf16_length = _utf16_length(text)
    text_end_offset = offset + text_utf16_length

    extracted_entities: list[SaveableEntity] = []
    for entity in entities:
        if entity.type != MessageEntityType.CUSTOM_EMOJI or not entity.custom_emoji_id:
            continue

        entity_end_offset = entity.offset + entity.length
        if entity.offset < offset or entity_end_offset > text_end_offset:
            continue

        extracted_entities.append(
            SaveableEntity(
                type="custom_emoji",
                offset=entity.offset - offset,
                length=entity.length,
                custom_emoji_id=entity.custom_emoji_id,
            )
        )

    return extracted_entities


def _get_message_entities(message: Message) -> list[MessageEntity] | None:
    if message.content_type == ContentType.TEXT:
        return message.entities

    return message.caption_entities


def parse_reply_message(message: Message) -> tuple[str, Optional[NoteFile], list[list[Button]], list[SaveableEntity]]:
    if message.content_type not in (*PARSABLE_CONTENT_TYPES, ContentType.TEXT):
        raise SophieException(
            Section(
                _("Please check the notes documentation for the list of the allowed content types."),
                title=_("Reply message content is not parsable as the note."),
            )
        )

    reply_markup = getattr(message, "reply_markup", None)
    buttons = parse_message_buttons(reply_markup) if reply_markup else []
    custom_emoji_entities = _extract_custom_emoji_entities(
        _get_message_entities(message), message.text or message.caption
    )

    return message.html_text, extract_file_info(message), buttons, custom_emoji_entities


async def parse_saveable(
    message: Message, text: Optional[str], allow_reply_message=True, buttons: ButtonsList | None = None, offset: int = 0
) -> Saveable:
    """Parses the given message and returns common note props to save."""
    # TODO: Make its own exception for notes saving
    note_text = text
    replied_buttons = []
    custom_emoji_entities = _extract_custom_emoji_entities(_get_message_entities(message), note_text, offset=offset)

    if allow_reply_message and message.reply_to_message and not message.reply_to_message.forum_topic_created:
        replied_message_text, file_data, replied_buttons, replied_custom_emoji_entities = parse_reply_message(
            message.reply_to_message
        )

        if replied_message_text and note_text:
            note_text = f"{replied_message_text}\n{note_text}"
            reply_shift = _utf16_length(f"{replied_message_text}\n")
            custom_emoji_entities = [
                SaveableEntity(
                    type="custom_emoji",
                    offset=entity_item.offset + reply_shift,
                    length=entity_item.length,
                    custom_emoji_id=entity_item.custom_emoji_id,
                )
                for entity_item in custom_emoji_entities
            ]
        elif replied_message_text:
            note_text = replied_message_text
            custom_emoji_entities = []

        custom_emoji_entities = replied_custom_emoji_entities + custom_emoji_entities

    else:
        file_data = extract_file_info(message)

    # Parse buttons (only when there's text to parse; file-only notes are allowed)
    if note_text and buttons is None:
        note_text, buttons = await parse_buttons_list_from_message(message, note_text, offset=offset)

    # If not specifically added
    if buttons is None:
        buttons = ButtonsList()

    buttons.extend(replied_buttons)

    # TODO: Length of the message with or without HTML entities??
    if len(note_text or "") > TELEGRAM_MESSAGE_LENGTH_LIMIT:
        raise SophieException(
            Section(
                Template(
                    _("The maximum length of the note is {limit} characters."), limit=TELEGRAM_MESSAGE_LENGTH_LIMIT
                ).to_html(),
                _("Please try to reduce the length of note."),
                title=_("Note is too long."),
            )
        )

    return Saveable(
        text=note_text,
        file=file_data,
        buttons=buttons,
        entities=custom_emoji_entities,
        version=CURRENT_SAVEABLE_VERSION,
    )
