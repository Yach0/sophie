from __future__ import annotations

from aiogram.enums import ContentType
from aiogram.types import Message
from stfu_tg import Section, Template

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList, parse_message_buttons
from sophie_bot.modules.notes.utils.buttons_processor.list_from_message import parse_buttons_list_from_message
from sophie_bot.modules.notes.utils.convert_to_html import preserve_custom_emoji_inline_html, tg_emoji_workaround
from sophie_bot.modules.notes.utils.media import (
    MEDIA_CAPTION_LENGTH_LIMIT,
    MEDIA_SPECS,
    PARSABLE_CONTENT_TYPES,
)
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _


def extract_file_info(message: Message) -> NoteFile | None:
    if message.content_type not in PARSABLE_CONTENT_TYPES:
        return None

    # Get file ID from the parsable fields
    attr = getattr(message, message.content_type, None)

    if not attr:
        return None

    # Photos are lists
    if isinstance(attr, list):
        attr = attr[-1]

    file_id: str | None = getattr(attr, "file_id", None)
    return NoteFile(id=file_id, type=ContentType(message.content_type)) if file_id else None


def parse_reply_message(message: Message) -> tuple[str, NoteFile | None, list[list[Button]]]:
    if message.content_type not in (*PARSABLE_CONTENT_TYPES, ContentType.TEXT):
        raise SophieException(
            Section(
                _("Please check the notes documentation for the list of the allowed content types."),
                title=_("Reply message content is not parsable as the note."),
            )
        )

    reply_markup = getattr(message, "reply_markup", None)
    buttons = parse_message_buttons(reply_markup) if reply_markup else []

    # aiogram's html_text property emits <tg-emoji emoji_id=...> but Telegram expects emoji-id (hyphenated),
    # so we fix the attribute name to ensure custom emoji render correctly.
    return tg_emoji_workaround(message.html_text), extract_file_info(message), buttons


async def parse_saveable(
    message: Message,
    text: str | None,
    allow_reply_message=True,
    buttons: ButtonsList | None = None,
    offset: int = 0,
    album: list[Message] | None = None,
) -> Saveable:
    """Parses the given message and returns common note props to save.

    ``album`` is the aggregated media-group messages (from the media-group middleware).
    When it holds more than one item the note stores every media file in ``files`` and
    leaves ``file`` unset; text/buttons still come from the representative message.
    """
    # TODO: Make its own exception for notes saving
    note_text = text
    initial_note_text = text
    replied_buttons = []
    files: list[NoteFile] = []

    if allow_reply_message and message.reply_to_message and not message.reply_to_message.forum_topic_created:
        replied_message_text, file_data, replied_buttons = parse_reply_message(message.reply_to_message)

        if replied_message_text and note_text:
            note_text = f"{replied_message_text}\n{note_text}"
        elif replied_message_text:
            note_text = replied_message_text

    else:
        file_data = extract_file_info(message)

    # Album (media group): gather a file from every item. This supersedes the single
    # `file_data` grabbed above (album[0] is the representative message itself).
    if album and len(album) > 1:
        files = [note_file for note_file in (extract_file_info(item) for item in album) if note_file]
        if files:
            file_data = None

    # Parse buttons (only when there's text to parse; file-only notes are allowed)
    if note_text and buttons is None:
        note_text, buttons = await parse_buttons_list_from_message(message, note_text, offset=offset)

    # If not specifically added
    if buttons is None:
        buttons = ButtonsList()

    if note_text and initial_note_text and note_text == initial_note_text:
        parsed_inline_html = preserve_custom_emoji_inline_html(message, text=note_text, offset=offset)
        if parsed_inline_html is not None:
            note_text = parsed_inline_html

    if note_text:
        note_text = tg_emoji_workaround(note_text)

    buttons.extend(replied_buttons)

    # A caption-carrying media note is capped far lower than a plain message; rejecting it
    # here keeps an over-long note from being saved and then failing on every retrieval.
    text_limit = (
        MEDIA_CAPTION_LENGTH_LIMIT
        if file_data and MEDIA_SPECS[file_data.type].supports_caption
        else TELEGRAM_MESSAGE_LENGTH_LIMIT
    )

    # TODO: Length of the message with or without HTML entities??
    if len(note_text or "") > text_limit:
        raise SophieException(
            Section(
                Template(_("The maximum length of the note is {limit} characters."), limit=text_limit).to_html(),
                _("Please try to reduce the length of note."),
                title=_("Note is too long."),
            )
        )

    return Saveable(text=note_text, file=file_data, files=files, buttons=buttons, version=CURRENT_SAVEABLE_VERSION)
