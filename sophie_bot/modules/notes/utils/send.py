from typing import Final

from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    SendMediaGroup,
    SendMessage,
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
from sophie_bot.modules.notes.utils._random_parser import parse_random_text
from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.notes.utils.fillings import process_fillings
from sophie_bot.modules.notes.utils.media import MEDIA_CAPTION_LENGTH_LIMIT, MEDIA_SPECS
from sophie_bot.modules.utils_.common_try import COROUTINE_TYPE, common_try
from sophie_bot.modules.utils_.telegram_exceptions import BOT_NOT_ADMIN
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _

# Kept below Telegram's 4096 so the rendered title and fillings cannot push the send over.
TEXT_LENGTH_LIMIT: Final[int] = 4090


def _build_input_media(note_file: NoteFile, caption: str | None) -> MediaUnion:
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
    reply_to: int | None,
    message_thread_id: int | None,
    collect_sent: list[Message] | None = None,
) -> Message | None:
    """Sends an album note via sendMediaGroup.

    sendMediaGroup accepts no reply_markup and caps captions at 1024 chars, so buttons
    and/or overflowing text are delivered in a follow-up message under the album.

    Only the first album message is returned, so callers that must account for every
    message the bot produced (silent-mode filters) pass `collect_sent` to receive them all.
    """
    has_buttons = bool(inline_markup.inline_keyboard)
    put_caption_on_album = bool(text) and len(text) <= MEDIA_CAPTION_LENGTH_LIMIT and not has_buttons

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
    if collect_sent is not None and isinstance(sent, list):
        collect_sent.extend(sent)

    need_followup = has_buttons or (bool(text) and not put_caption_on_album)
    if need_followup:
        # Invisible separator keeps a button-only follow-up (file album, no caption) non-empty.
        followup = await common_try(
            to_try=SendMessage(
                chat_id=send_to,
                text=text or "⁣",
                reply_markup=inline_markup if has_buttons else None,
                reply_parameters=(ReplyParameters(message_id=first_message.message_id) if first_message else None),
                message_thread_id=message_thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ).emit(bot)
        )
        if collect_sent is not None and isinstance(followup, Message):
            collect_sent.append(followup)

    return first_message


# Telegram accepts only this many ephemeral messages per user, so anything that needs more than one
# message — an album — has to be posted to the chat instead.
MAX_EPHEMERAL_MESSAGES_PER_USER = 5


async def send_saveable(
    message: Message | None,
    send_to: int,
    saveable: Saveable,
    reply_to: int | None = None,
    title: Element | None = None,
    raw: bool | None = False,
    additional_keyboard: InlineKeyboardMarkup | None = None,
    additional_fillings: dict[str, str] | None = None,
    connection: ChatConnection | None = None,
    user: User | None = None,
    message_thread_id: int | None = None,
    collect_sent: list[Message] | None = None,
    receiver_user_id: int | None = None,
) -> Message | None:
    """Sends a saveable, returning its primary message.

    An album produces several messages but only the first is returned; pass `collect_sent`
    to receive every message that was actually sent.

    ``receiver_user_id`` makes the message ephemeral: only that member sees it, and it is not part
    of the chat history. Albums are always sent normally: sendMediaGroup takes no receiver, and
    splitting one into separate ephemeral sends is no way around it, since a user can be sent at
    most MAX_EPHEMERAL_MESSAGES_PER_USER of them.
    """
    text = saveable.text or ""

    # An album moves overflowing text into a follow-up message, so only a single caption-bearing
    # media file subjects the note text to the caption limit.
    is_album = len(saveable.files) > 1
    single_file = None if is_album else (saveable.file or (saveable.files[0] if saveable.files else None))

    # Note - the order of those operations are actually more important than whatd you think
    # We want to extract the buttons as the very first, since laterly, the markdown convertor would convert them to the normal URLs, which we don't want!
    # And we want to process the fillings the last, as they produce formatting HTML formatting that would be escaped.

    # Extract buttons
    inline_markup = InlineKeyboardMarkup(inline_keyboard=[])
    if not raw:
        chat_id_for_buttons = connection.db_model.tid if connection else (message.chat.id if message else send_to)

        inline_markup = render_buttons(saveable.buttons, chat_id_for_buttons)

        # Defensive net: parse legacy button syntax from text for persisted saveables
        # that predate the structured button model. Kept permanently as a safety net.
        if not inline_markup.inline_keyboard and text:
            cleaned_text, legacy_buttons = parse_legacy_text_buttons(text)
            if legacy_buttons:
                text = cleaned_text
                inline_markup = render_buttons(legacy_buttons, chat_id_for_buttons)

        if additional_keyboard:
            inline_markup.inline_keyboard.extend(additional_keyboard.inline_keyboard)

    # Process fillings
    text = process_fillings(text, message, user or (message.from_user if message else None), additional_fillings)

    # Add title
    text = (str(title) + "\n" if title else "") + text

    # Apply random choice sections (%%%...%%%)
    if text:
        text = parse_random_text(text)

    text_limit = (
        MEDIA_CAPTION_LENGTH_LIMIT
        if single_file and MEDIA_SPECS[single_file.type].supports_caption
        else TEXT_LENGTH_LIMIT
    )
    if len(text) > text_limit:
        raise SophieException(_("The text is too long"))

    # Media group (album): more than one stored file → send via sendMediaGroup
    # (Telegram requires 2-10 items). A degenerate single-item album falls through
    # to the single-media path below.
    if is_album:
        return await _send_media_group(
            send_to=send_to,
            files=saveable.files,
            text=text,
            inline_markup=inline_markup,
            reply_to=reply_to,
            message_thread_id=message_thread_id,
            collect_sent=collect_sent,
        )

    # TODO: Multi messages

    method: type[TelegramMethod[Message]]
    kwargs: dict[str, object] = {"chat_id": send_to, "reply_markup": inline_markup}
    if receiver_user_id is not None:
        kwargs["receiver_user_id"] = receiver_user_id

    if single_file:
        media_spec = MEDIA_SPECS[single_file.type]
        method = media_spec.method
        kwargs[media_spec.file_field] = single_file.id
        if media_spec.supports_caption:
            kwargs["caption"] = text
    else:
        method = SendMessage
        kwargs["text"] = text
        # TODO: Settings?
        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)

    if reply_to:
        kwargs["reply_parameters"] = ReplyParameters(message_id=reply_to)
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    def to_try(**cb_kwargs: object) -> COROUTINE_TYPE:
        return method(**cb_kwargs).emit(bot)

    async def reply_not_found() -> Message | None:
        kwargs.pop("reply_parameters", None)
        return await to_try(**kwargs)

    try:
        sent = await common_try(to_try=to_try(**kwargs), reply_not_found=reply_not_found)
    except TelegramBadRequest as error:
        if BOT_NOT_ADMIN not in error.message:
            raise
        sent = None
    if collect_sent is not None and isinstance(sent, Message):
        collect_sent.append(sent)

    return sent
