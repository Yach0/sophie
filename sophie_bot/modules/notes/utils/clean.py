from collections.abc import Sequence

from aiogram.filters import CommandObject
from aiogram.types import Message

from sophie_bot.db.models.clean_notes import CleanNotesModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import is_enabled

FORMATTING_MODIFIERS: tuple[str, ...] = ("noformat", "?raw")


def is_standalone_command_request(command: CommandObject | None, note_name: str) -> bool:
    """Whether a `/get` message carries nothing but the note request.

    The command, its prefix and any `@botname` mention are already parsed out, so only the
    arguments are left to check: they must be exactly the note name plus, at most, one of the
    formatting modifiers the handler accepts. Anything else the member typed keeps the message.
    """
    if not command:
        return False

    arguments = " ".join((command.args or "").split()).removeprefix("#")
    if arguments == note_name:
        return True

    return any(arguments == f"{note_name} {modifier}" for modifier in FORMATTING_MODIFIERS)


def is_standalone_hashtag_request(text: str | None, note_names: Sequence[str]) -> bool:
    """Whether the whole message is a single `#name` note request.

    A note request surrounded by any other text belongs to the conversation, so it is never
    treated as disposable. Deleting a member's message cannot be undone, so anything less
    obvious than one bare request - several hashtags at once included - is kept.
    """
    if len(note_names) != 1:
        return False

    return (text or "").strip() == f"#{note_names[0]}"


async def clean_notes(
    connection: ChatConnection,
    message: Message,
    sent_messages: Sequence[Message],
    *,
    request_is_standalone: bool,
) -> None:
    """Removes the previously sent note, and the request itself when it is a standalone one.

    Only messages of the chat the note was sent to are touched, so a note fetched over a
    connection from another chat leaves both chats alone.
    """
    if message.chat.id != connection.tid:
        return

    if not await is_enabled("cleannotes", chat_tid=connection.tid):
        return

    db_model = await CleanNotesModel.get_by_chat_iid(connection.db_model.iid)
    if not db_model.enabled:
        return

    to_delete: list[int] = list(db_model.last_msgs)

    if request_is_standalone:
        to_delete.append(message.message_id)

    # Always rewrites the tracked ids: a note that failed to send leaves nothing to clean next time.
    await db_model.new_messages([sent.message_id for sent in sent_messages])

    if to_delete:
        await common_try(bot.delete_messages(chat_id=message.chat.id, message_ids=to_delete))
