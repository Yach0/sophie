import re
from collections.abc import Sequence

from aiogram.types import Message

from sophie_bot.config import CONFIG
from sophie_bot.db.models.clean_notes import CleanNotesModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import is_enabled

# Same shape the hashtag note handler accepts, anchored: nothing but the note request may match.
NOTE_NAME_PATTERN = r"[\w/.+=-]*[\w/+=-]"

_HASHTAG_REQUEST = re.compile(rf"^#{NOTE_NAME_PATTERN}$")
_COMMAND_REQUEST = re.compile(
    rf"^[{re.escape(CONFIG.commands_prefix)}]get(?:@\w+)?\s+#?{NOTE_NAME_PATTERN}(?:\s+(?:noformat|raw))?$",
    re.IGNORECASE,
)


def is_standalone_note_request(text: str | None) -> bool:
    """Whether the whole message is a single note request (`/get name` or `#name`).

    A note request surrounded by any other text belongs to the conversation, so it is never
    treated as disposable. Deleting a member's message cannot be undone, so anything less
    obvious than one bare request - several hashtags at once included - is kept.
    """
    if not text:
        return False

    stripped = text.strip()
    return bool(_HASHTAG_REQUEST.match(stripped) or _COMMAND_REQUEST.match(stripped))


async def clean_notes(connection: ChatConnection, message: Message, sent_messages: Sequence[Message]) -> None:
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

    if is_standalone_note_request(message.text):
        to_delete.append(message.message_id)

    # Always rewrites the tracked ids: a note that failed to send leaves nothing to clean next time.
    await db_model.new_messages([sent.message_id for sent in sent_messages])

    if to_delete:
        await common_try(bot.delete_messages(chat_id=message.chat.id, message_ids=to_delete))
