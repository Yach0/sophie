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
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReplyParameters,
    User,
)
from stfu_tg.doc import Element

from sophie_bot.db.models.notes import Saveable, SaveableEntity, SaveableParseMode
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.notes.utils.buttons_processor.legacy import legacy_button_parser
from sophie_bot.modules.notes.utils.buttons_processor.unparse import unparse_buttons
from sophie_bot.modules.notes.utils.fillings import process_fillings
from sophie_bot.modules.notes.utils.parse import (
    PARSABLE_CONTENT_TYPES,
    SUPPORTS_CAPTION,
)
from sophie_bot.modules.notes.utils.random_parser import parse_random_text
from sophie_bot.modules.notes.utils.unparse_legacy import legacy_markdown_to_html
from sophie_bot.modules.utils_.common_try import common_try
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


async def send_saveable(
    message: Optional[Message],
    send_to: int,
    saveable: Saveable,
    reply_to: Optional[int] = None,
    title: Optional[Element] = None,
    raw: Optional[bool] = False,
    additional_keyboard: InlineKeyboardMarkup = InlineKeyboardMarkup(inline_keyboard=[]),
    additional_fillings: Optional[dict[str, str]] = None,
    connection: ChatConnection | None = None,
    user: Optional[User] = None,
):
    def utf16_length(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    def should_send_custom_emoji_entities(source_text: str, source_entities: list[SaveableEntity]) -> bool:
        has_html_like_tags = "<" in source_text and ">" in source_text
        return bool(source_entities) and not has_html_like_tags

    def shift_custom_emoji_entities(source_entities: list[SaveableEntity], shift: int) -> list[SaveableEntity]:
        if shift <= 0:
            return source_entities

        return [
            SaveableEntity(
                type="custom_emoji",
                offset=entity_item.offset + shift,
                length=entity_item.length,
                custom_emoji_id=entity_item.custom_emoji_id,
            )
            for entity_item in source_entities
        ]

    def to_message_entities(source_entities: list[SaveableEntity]) -> list[MessageEntity]:
        return [
            MessageEntity(
                type=entity_item.type,
                offset=entity_item.offset,
                length=entity_item.length,
                custom_emoji_id=entity_item.custom_emoji_id,
            )
            for entity_item in source_entities
        ]

    text = saveable.text or ""
    custom_emoji_entities = saveable.entities

    # Note - the order of those operations are actually more important than whatd you think
    # We want to extract the buttons as the very first, since laterly, the markdown convertor would convert them to the normal URLs, which we don't want!
    # And we want to process the fillings the last, as they produce formatting HTML formatting that would be escaped.

    # Extract buttons
    inline_markup = InlineKeyboardMarkup(inline_keyboard=[])
    if not raw:
        chat_id_for_buttons = connection.db_model.tid if connection else (message.chat.id if message else send_to)

        if saveable.version == 1:
            text, inline_markup = legacy_button_parser(chat_id_for_buttons, text)
        else:
            inline_markup = unparse_buttons(saveable.buttons, chat_id_for_buttons)

        inline_markup.inline_keyboard.extend(additional_keyboard.inline_keyboard)

    # Convert legacy markdown to HTML
    if text and saveable.parse_mode != SaveableParseMode.html:
        text = legacy_markdown_to_html(text)

    # Process fillings
    text = process_fillings(text, message, user or (message.from_user if message else None), additional_fillings)

    # Add title
    title_prefix = str(title) + "\n" if title else ""
    text = title_prefix + text
    if title_prefix:
        custom_emoji_entities = shift_custom_emoji_entities(custom_emoji_entities, utf16_length(title_prefix))

    # Apply random choice sections (%%%...%%%)
    if text:
        text = parse_random_text(text)

    if len(text) > 4090:
        raise SophieException(_("The text is too long"))

    # TODO: Media groups
    # TODO: Multi messages

    content_type = saveable.file.type if saveable.file else ContentType.TEXT

    kwargs = {
        "chat_id": send_to,
        "text": text,
    }
    can_send_custom_emoji_entities = should_send_custom_emoji_entities(text, custom_emoji_entities)

    # Text
    if content_type == ContentType.TEXT:
        kwargs["text"] = text
        kwargs["reply_markup"] = inline_markup
        if can_send_custom_emoji_entities:
            kwargs["entities"] = to_message_entities(custom_emoji_entities)
            kwargs["parse_mode"] = None

        # TODO: Settings?
        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
    elif content_type in SUPPORTS_CAPTION:
        kwargs["caption"] = text
        kwargs["reply_markup"] = inline_markup
        if can_send_custom_emoji_entities:
            kwargs["caption_entities"] = to_message_entities(custom_emoji_entities)
            kwargs["parse_mode"] = None

    # File
    if content_type == ContentType.TEXT:
        pass
    elif content_type in PARSABLE_CONTENT_TYPES and saveable.file:
        kwargs[content_type] = saveable.file.id
    elif not saveable.file:
        raise ValueError(f"Unsupported content type: {content_type}")

    if reply_to:
        kwargs["reply_parameters"] = ReplyParameters(message_id=reply_to)

    def to_try(**cb_kwargs):
        return SEND_METHOD[content_type](**cb_kwargs).emit(bot)

    async def reply_not_found():
        if "reply_parameters" in kwargs:
            del kwargs["reply_parameters"]
        return await to_try(**kwargs)

    return await common_try(to_try=to_try(**kwargs), reply_not_found=reply_not_found)
