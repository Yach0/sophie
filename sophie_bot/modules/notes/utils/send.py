from typing import Optional, Type

from aiogram.enums import ContentType
from aiogram.methods import (
    SendAnimation,
    SendAudio,
    SendContact,
    SendDice,
    SendDocument,
    SendGame,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendPoll,
    SendSticker,
    SendVenue,
    SendVideo,
    SendVoice,
    TelegramMethod,
)
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    MediaUnion,
    Message,
    ReplyParameters,
    User,
)
from stfu_tg.doc import Element

from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.notes.utils.fillings import process_fillings
from sophie_bot.modules.notes.utils.parse import (
    PARSABLE_CONTENT_TYPES,
    SUPPORTS_CAPTION,
)
from sophie_bot.modules.notes.utils._random_parser import parse_random_text
from sophie_bot.modules.utils_.common_try import COROUTINE_TYPE, common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _

SEND_METHOD: dict[ContentType, Type[TelegramMethod[Message]]] = {
    ContentType.TEXT: SendMessage,
    ContentType.AUDIO: SendAudio,
    ContentType.ANIMATION: SendAnimation,
    ContentType.DOCUMENT: SendDocument,
    ContentType.GAME: SendGame,
    ContentType.PHOTO: SendPhoto,
    ContentType.STICKER: SendSticker,
    ContentType.VIDEO: SendVideo,
    ContentType.VIDEO_NOTE: SendVideo,
    ContentType.VOICE: SendVoice,
    ContentType.CONTACT: SendContact,
    ContentType.VENUE: SendVenue,
    ContentType.LOCATION: SendLocation,
    ContentType.POLL: SendPoll,
    ContentType.DICE: SendDice,
}

# Telegram caps media-group captions at 1024 characters.
MEDIA_CAPTION_LIMIT = 1024


def _build_input_media(note_file: NoteFile, caption: Optional[str]) -> MediaUnion:
    """Builds a sendMediaGroup item from a stored note file.

    Only photo/video/document/audio are groupable; albums never contain other types.
    Anything unexpected falls back to a document so the send does not crash.
    """
    if note_file.type == ContentType.VIDEO:
        return InputMediaVideo(media=note_file.id, caption=caption)
    if note_file.type == ContentType.AUDIO:
        return InputMediaAudio(media=note_file.id, caption=caption)
    if note_file.type == ContentType.PHOTO:
        return InputMediaPhoto(media=note_file.id, caption=caption)
    return InputMediaDocument(media=note_file.id, caption=caption)


async def _send_media_group(
    send_to: int,
    files: list[NoteFile],
    text: str,
    inline_markup: InlineKeyboardMarkup,
    reply_to: Optional[int],
    message_thread_id: int | None,
) -> Message | None:
    """Sends an album note via sendMediaGroup.

    sendMediaGroup accepts no reply_markup and caps captions at 1024 chars, so buttons
    and/or overflowing text are delivered in a follow-up message under the album.
    """
    has_buttons = bool(inline_markup.inline_keyboard)
    put_caption_on_album = bool(text) and len(text) <= MEDIA_CAPTION_LIMIT and not has_buttons

    media: list[MediaUnion] = [
        _build_input_media(note_file, text if index == 0 and put_caption_on_album else None)
        for index, note_file in enumerate(files)
    ]

    reply_parameters = ReplyParameters(message_id=reply_to) if reply_to else None

    def to_try(with_reply: bool) -> COROUTINE_TYPE:
        return SendMediaGroup(
            chat_id=send_to,
            media=media,
            reply_parameters=reply_parameters if with_reply else None,
            message_thread_id=message_thread_id,
        ).emit(bot)

    sent = await common_try(
        to_try=to_try(with_reply=True),
        reply_not_found=lambda: to_try(with_reply=False),
    )
    first_message = sent[0] if isinstance(sent, list) and sent else None

    need_followup = has_buttons or (bool(text) and not put_caption_on_album)
    if need_followup:
        # Invisible separator keeps a button-only follow-up (file album, no caption) non-empty.
        await common_try(
            to_try=SendMessage(
                chat_id=send_to,
                text=text or "⁣",
                reply_markup=inline_markup if has_buttons else None,
                reply_parameters=(ReplyParameters(message_id=first_message.message_id) if first_message else None),
                message_thread_id=message_thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ).emit(bot)
        )

    return first_message


async def send_saveable(
    message: Optional[Message],
    send_to: int,
    saveable: Saveable,
    reply_to: Optional[int] = None,
    title: Optional[Element] = None,
    raw: Optional[bool] = False,
    additional_keyboard: InlineKeyboardMarkup | None = None,
    additional_fillings: Optional[dict[str, str]] = None,
    connection: ChatConnection | None = None,
    user: Optional[User] = None,
    message_thread_id: int | None = None,
) -> Message | None:
    text = saveable.text or ""

    # Note - the order of those operations are actually more important than whatd you think
    # We want to extract the buttons as the very first, since laterly, the markdown convertor would convert them to the normal URLs, which we don't want!
    # And we want to process the fillings the last, as they produce formatting HTML formatting that would be escaped.

    # Extract buttons
    inline_markup = InlineKeyboardMarkup(inline_keyboard=[])
    if not raw:
        chat_id_for_buttons = connection.db_model.tid if connection else (message.chat.id if message else send_to)

        inline_markup = render_buttons(saveable.buttons, chat_id_for_buttons)

        if additional_keyboard:
            inline_markup.inline_keyboard.extend(additional_keyboard.inline_keyboard)

    # Process fillings
    text = process_fillings(text, message, user or (message.from_user if message else None), additional_fillings)

    # Add title
    text = (str(title) + "\n" if title else "") + text

    # Apply random choice sections (%%%...%%%)
    if text:
        text = parse_random_text(text)

    if len(text) > 4090:
        raise SophieException(_("The text is too long"))

    # Media group (album): more than one stored file → send via sendMediaGroup
    # (Telegram requires 2-10 items). A degenerate single-item album falls through
    # to the single-media path below.
    if saveable.files and len(saveable.files) > 1:
        return await _send_media_group(
            send_to=send_to,
            files=saveable.files,
            text=text,
            inline_markup=inline_markup,
            reply_to=reply_to,
            message_thread_id=message_thread_id,
        )

    # TODO: Multi messages

    single_file = saveable.file or (saveable.files[0] if saveable.files else None)
    content_type = single_file.type if single_file else ContentType.TEXT

    kwargs: dict[str, object] = {"chat_id": send_to}

    if content_type == ContentType.TEXT:
        kwargs["text"] = text
        kwargs["reply_markup"] = inline_markup
        # TODO: Settings?
        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
    else:
        if not single_file:
            raise ValueError(f"Unsupported content type: {content_type}")
        # The media file id is keyed by the content-type name (e.g. photo=<id>).
        if content_type in PARSABLE_CONTENT_TYPES:
            kwargs[content_type] = single_file.id
        # Caption-supporting media carry the note text as a caption plus the buttons.
        if content_type in SUPPORTS_CAPTION:
            kwargs["caption"] = text
            kwargs["reply_markup"] = inline_markup

    if reply_to:
        kwargs["reply_parameters"] = ReplyParameters(message_id=reply_to)
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    def to_try(**cb_kwargs: object) -> COROUTINE_TYPE:
        return SEND_METHOD[content_type](**cb_kwargs).emit(bot)

    async def reply_not_found() -> Message | None:
        if "reply_parameters" in kwargs:
            del kwargs["reply_parameters"]
        return await to_try(**kwargs)

    return await common_try(to_try=to_try(**kwargs), reply_not_found=reply_not_found)
