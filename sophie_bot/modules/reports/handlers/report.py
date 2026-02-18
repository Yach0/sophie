from __future__ import annotations

from aiogram import F, Router, flags
from stfu_tg import Code, Doc, HList, InvisibleSymbol, KeyValue, Template, UserLink

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Reports the replied message."))
@flags.disableable(name="report")
class ReportHandler(SophieMessageHandler):
    @staticmethod
    def filters():
        return ()

    @classmethod
    def register(cls, router: Router):
        router.message.register(cls, CMDFilter(("report",)), F.chat.type.in_({"group", "supergroup"}))
        router.message.register(cls, F.text.regexp(r"^@admin(s)?$"), F.chat.type.in_({"group", "supergroup"}))

    async def handle(self):
        message = self.event
        if not message.from_user:
            return

        user_id = message.from_user.id
        chat_id = message.chat.id

        # Check if user is admin
        if await is_user_admin(chat_id, user_id):
            await message.reply(_("You are an admin, you don't need to report."))
            return

        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.reply(_("You need to reply to a message to report it."))
            return

        offender_id = message.reply_to_message.from_user.id
        if await is_user_admin(chat_id, offender_id):
            await message.reply(_("You cannot report an admin."))
            return

        # Get admins
        chat = await ChatModel.get_by_tid(chat_id)
        if not chat:
            return

        # Fetch admins using ChatAdminModel
        admins = await ChatAdminModel.find(
            ChatAdminModel.chat.id == chat.iid,
            fetch_links=True,
        ).to_list()

        # Build message
        offender_mention = UserLink(offender_id, message.reply_to_message.from_user.full_name)

        doc = Doc()

        # Mention admins
        mentions = [
            UserLink(admin.user.tid, InvisibleSymbol())
            if hasattr(admin.user, "tid")
            else UserLink(admin.user.chat_id, InvisibleSymbol())
            for admin in admins
            if admin.user
        ]

        # We add mentions right after this line, because if we add them at the last line, they would make the message bubble bigger
        doc += HList(
            Template(_("User {user} ({user_id}) has been reported!"), user=offender_mention, user_id=Code(offender_id)),
            *mentions,
            divider="",
        )

        doc += KeyValue(_("Reported by"), UserLink(message.from_user.id, message.from_user.full_name))

        # Add reason if present
        # message.text is guaranteed to exist by CMDFilter usually, but good to check
        if message.text:
            command_args = message.text.split(maxsplit=1)
            if len(command_args) > 1:
                doc += KeyValue(_("Reason"), command_args[1])

        await message.reply(str(doc))
