from __future__ import annotations

from typing import Final, TypedDict

from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    InlineQuery,
    Message,
    TelegramObject,
    Update,
)

from sophie_bot.config import CONFIG


class UpdateInfo(TypedDict):
    """Labeling attributes extracted from an incoming update."""

    update_type: str
    chat_type: str
    transport: str
    message_kind: str | None


# (message attribute, kind label) — checked in order, first truthy match wins
MESSAGE_KIND_MAP: Final[list[tuple[str, str]]] = [
    ("text", "text"),
    ("photo", "photo"),
    ("video", "video"),
    ("audio", "audio"),
    ("voice", "voice"),
    ("document", "document"),
    ("sticker", "sticker"),
    ("animation", "animation"),
    ("video_note", "video_note"),
    ("contact", "contact"),
    ("location", "location"),
    ("venue", "venue"),
    ("poll", "poll"),
    ("dice", "dice"),
    ("game", "game"),
    ("invoice", "invoice"),
    ("successful_payment", "successful_payment"),
    ("connected_website", "connected_website"),
    ("passport_data", "passport_data"),
    ("proximity_alert_triggered", "proximity_alert"),
    ("forum_topic_created", "forum_topic_created"),
    ("forum_topic_closed", "forum_topic_closed"),
    ("forum_topic_reopened", "forum_topic_reopened"),
    ("general_forum_topic_hidden", "general_forum_topic_hidden"),
    ("general_forum_topic_unhidden", "general_forum_topic_unhidden"),
    ("write_access_allowed", "write_access_allowed"),
    ("user_shared", "user_shared"),
    ("chat_shared", "chat_shared"),
    ("new_chat_members", "new_chat_members"),
    ("left_chat_member", "left_chat_member"),
    ("new_chat_title", "new_chat_title"),
    ("new_chat_photo", "new_chat_photo"),
    ("delete_chat_photo", "delete_chat_photo"),
    ("group_chat_created", "group_chat_created"),
    ("supergroup_chat_created", "supergroup_chat_created"),
    ("channel_chat_created", "channel_chat_created"),
    ("migrate_to_chat_id", "migrate_to_chat_id"),
    ("migrate_from_chat_id", "migrate_from_chat_id"),
    ("pinned_message", "pinned_message"),
]


def get_message_kind(message: Message) -> str:
    """Determine message kind for labeling."""
    for attr, kind in MESSAGE_KIND_MAP:
        if getattr(message, attr, None):
            return kind
    return "other"


def extract_update_info(event: TelegramObject) -> UpdateInfo:
    """Extract update information for labeling."""

    update_type = "unknown"
    chat_type = "unknown"
    transport = "webhook" if CONFIG.webhooks_enable else "polling"
    message_kind: str | None = None

    # Extract update type and chat type
    if isinstance(event, Update):
        # Determine update type
        if event.message:
            update_type = "message"
            message_kind = get_message_kind(event.message)
            chat_type = event.message.chat.type if event.message.chat else "unknown"
        elif event.edited_message:
            update_type = "edited_message"
            message_kind = get_message_kind(event.edited_message)
            chat_type = event.edited_message.chat.type if event.edited_message.chat else "unknown"
        elif event.callback_query:
            update_type = "callback_query"
            chat_type = (
                event.callback_query.message.chat.type
                if (event.callback_query.message and event.callback_query.message.chat)
                else "unknown"
            )
        elif event.inline_query:
            update_type = "inline_query"
            chat_type = "inline"
        elif event.chat_member:
            update_type = "chat_member"
            chat_type = event.chat_member.chat.type if event.chat_member.chat else "unknown"
        elif event.my_chat_member:
            update_type = "my_chat_member"
            chat_type = event.my_chat_member.chat.type if event.my_chat_member.chat else "unknown"
        elif event.chat_join_request:
            update_type = "chat_join_request"
            chat_type = event.chat_join_request.chat.type if event.chat_join_request.chat else "unknown"
    elif isinstance(event, Message):
        update_type = "message"
        message_kind = get_message_kind(event)
        chat_type = event.chat.type if event.chat else "unknown"
    elif isinstance(event, CallbackQuery):
        update_type = "callback_query"
        chat_type = event.message.chat.type if (event.message and event.message.chat) else "unknown"
    elif isinstance(event, InlineQuery):
        update_type = "inline_query"
        chat_type = "inline"
    elif isinstance(event, ChatMemberUpdated):
        update_type = "chat_member"
        chat_type = event.chat.type if event.chat else "unknown"
    elif isinstance(event, ChatJoinRequest):
        update_type = "chat_join_request"
        chat_type = event.chat.type if event.chat else "unknown"

    return {
        "update_type": update_type,
        "chat_type": chat_type,
        "transport": transport,
        "message_kind": message_kind,
    }


def _get_command_prefix(text: str) -> str | None:
    prefixes: list[str] = [str(prefix) for prefix in CONFIG.commands_prefix]
    if not prefixes:
        return None

    ordered_prefixes = sorted(prefixes, key=len, reverse=True)
    for prefix_value in ordered_prefixes:
        prefix = str(prefix_value)
        if text.startswith(prefix):
            return prefix

    return None


def extract_command_name(event: TelegramObject) -> str | None:
    """Extract the command name (without prefix or bot mention) from an update, if any."""
    message: Message | None = None

    if isinstance(event, Update):
        message = event.message or event.edited_message
    elif isinstance(event, Message):
        message = event

    if message is None or not message.text:
        return None

    text = message.text.strip()
    command_prefix = _get_command_prefix(text)
    if command_prefix is None:
        return None

    first_token = text.split(" ", maxsplit=1)[0]
    command_without_mention = first_token.split("@", maxsplit=1)[0]
    command_name = command_without_mention.removeprefix(command_prefix).strip().lower()

    if not command_name:
        return None

    return command_name[:50]
