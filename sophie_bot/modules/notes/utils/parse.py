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
from sophie_bot.modules.notes.utils.rich import (
    rich_message_to_html_fallback,
    validate_rich_message_source,
    validate_rich_message_structure,
)
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _


def extract_file_info(message: Message) -> NoteFile | None:
    if message.content_type not in PARSABLE_CONTENT_TYPES:
        return None

    attr = getattr(message, message.content_type, None)
    if not attr:
        return None

    if isinstance(attr, list):
        attr = attr[-1]

    file_id: str | None = getattr(attr, "file_id", None)
    return NoteFile(id=file_id, type=ContentType(message.content_type)) if file_id else None


def parse_reply_message(message: Message) -> tuple[str, NoteFile | None, list[list[Button]]]:
    rich_message = getattr(message, "rich_message", None)
    if rich_message is not None:
        reply_markup = getattr(message, "reply_markup", None)
        buttons = parse_message_buttons(reply_markup) if reply_markup else []
        return rich_message_to_html_fallback(rich_message), None, buttons

    if message.content_type not in (*PARSABLE_CONTENT_TYPES, ContentType.TEXT):
        raise SophieException(
            Section(
                _("Please check the notes documentation for the list of the allowed content types."),
                title=_("Reply message content is not parsable as the note."),
            )
        )

    reply_markup = getattr(message, "reply_markup", None)
    buttons = parse_message_buttons(reply_markup) if reply_markup else []
    return tg_emoji_workaround(message.html_text), extract_file_info(message), buttons


async def _rich_saveable(
    source_message: Message,
    *,
    owner_chat_tid: int | None,
    buttons: ButtonsList,
) -> Saveable:
    if not await is_enabled("saveable_rich_messages", chat_tid=owner_chat_tid):
        raise SophieException(
            Section(
                _("This message type is not supported for notes yet."),
                title=_("Reply message content is not parsable as the note."),
            )
        )

    rich_message = source_message.rich_message
    if rich_message is None:
        raise ValueError("Rich source disappeared while parsing")
    try:
        validate_rich_message_structure(rich_message)
        validate_rich_message_source(source_message, bot_user_id=getattr(bot, "id", None))
    except ValueError as exc:
        raise SophieException(Section(str(exc), title=_("Rich message cannot be saved."))) from exc

    source_markup = getattr(source_message, "reply_markup", None)
    if source_markup:
        buttons.extend(parse_message_buttons(source_markup))
    return Saveable(
        text=rich_message_to_html_fallback(rich_message),
        file=None,
        files=[],
        buttons=buttons,
        rich_message=rich_message,
        version=CURRENT_SAVEABLE_VERSION,
    )


async def parse_saveable(
    message: Message,
    text: str | None,
    allow_reply_message: bool = True,
    buttons: ButtonsList | None = None,
    offset: int = 0,
    album: list[Message] | None = None,
    *,
    owner_chat_tid: int | None = None,
) -> Saveable:
    """Parse a Telegram message into the shared ordinary or Rich Saveable contract."""
    note_text = text
    initial_note_text = text
    replied_buttons: list[list[Button]] = []
    files: list[NoteFile] = []

    rich_source: Message | None = None
    if getattr(message, "rich_message", None) is not None:
        rich_source = message
    elif (
        allow_reply_message
        and message.reply_to_message
        and not message.reply_to_message.forum_topic_created
        and getattr(message.reply_to_message, "rich_message", None) is not None
    ):
        rich_source = message.reply_to_message

    if rich_source is not None:
        if note_text:
            raise SophieException(
                Section(
                    _("Rich messages cannot be combined with additional note text."),
                    title=_("Rich message cannot be saved."),
                )
            )
        rich_buttons = buttons if buttons is not None else ButtonsList()
        return await _rich_saveable(rich_source, owner_chat_tid=owner_chat_tid or message.chat.id, buttons=rich_buttons)

    if allow_reply_message and message.reply_to_message and not message.reply_to_message.forum_topic_created:
        replied_message_text, file_data, replied_buttons = parse_reply_message(message.reply_to_message)
        if replied_message_text and note_text:
            note_text = f"{replied_message_text}\n{note_text}"
        elif replied_message_text:
            note_text = replied_message_text
    else:
        file_data = extract_file_info(message)

    if album and len(album) > 1:
        files = [note_file for note_file in (extract_file_info(item) for item in album) if note_file]
        if files:
            file_data = None

    if note_text and buttons is None:
        note_text, buttons = await parse_buttons_list_from_message(message, note_text, offset=offset)

    if buttons is None:
        buttons = ButtonsList()

    if note_text and initial_note_text and note_text == initial_note_text:
        parsed_inline_html = preserve_custom_emoji_inline_html(message, text=note_text, offset=offset)
        if parsed_inline_html is not None:
            note_text = parsed_inline_html

    if note_text:
        note_text = tg_emoji_workaround(note_text)

    buttons.extend(replied_buttons)
    text_limit = (
        MEDIA_CAPTION_LENGTH_LIMIT
        if file_data and MEDIA_SPECS[file_data.type].supports_caption
        else TELEGRAM_MESSAGE_LENGTH_LIMIT
    )
    if len(note_text or "") > text_limit:
        raise SophieException(
            Section(
                Template(_("The maximum length of the note is {limit} characters."), limit=text_limit).to_html(),
                _("Please try to reduce the length of note."),
                title=_("Note is too long."),
            )
        )

    return Saveable(text=note_text, file=file_data, files=files, buttons=buttons, version=CURRENT_SAVEABLE_VERSION)
