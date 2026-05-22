from re import search
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import CommandStart
from stfu_tg import Bold, HList, Title

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_NOTE_BUTTON_PATTERN, LEGACY_NOTE_BUTTON_PREFIX
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


class LegacyStartNoteButton(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CommandStart(deep_link=True, magic=F.args.regexp(LEGACY_NOTE_BUTTON_PREFIX)),)

    async def handle(self) -> Any:
        message = self.event

        regex = search(LEGACY_NOTE_BUTTON_PATTERN, message.text)

        if not regex:
            return

        chat_tid = int(regex.group(2))
        user_id = message.from_user.id
        note_name = regex.group(1)

        chat = await ChatModel.get_by_tid(chat_tid)
        if not chat:
            raise SophieException("Chat not found")

        note = await NoteModel.get_by_notenames(chat.iid, (note_name,))

        if not note:
            await message.reply(_("This note no longer exists."))
            return

        title = Bold(HList(Title(f"📗 #{note_name}", bold=False), note.description or ""))

        note_connection = ChatConnection(
            type=chat.type,
            is_connected=False,
            tid=chat.tid,
            title=chat.first_name_or_title,
            db_model=chat,
        )

        await send_saveable(
            message,
            user_id,
            note,
            title=title,
            reply_to=message.message_id,
            connection=note_connection,
        )
