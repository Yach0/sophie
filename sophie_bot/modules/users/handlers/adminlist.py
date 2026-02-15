from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import UserLink, VList, Template, Section

from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Lists all the chats admins."))
@flags.disableable(name="adminlist")
class AdminListHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("adminlist", "admins")),)

    async def handle(self) -> Any:
        if self.event.chat.type == "private":
            return await self.event.reply(_("This command can only be used in groups."))

        chat_model = await ChatModel.get_by_tid(self.connection.tid)
        if not chat_model:
            return await self.event.reply(_("Chat not found."))

        # Fetch admins from DB
        # Note: ChatAdminModel stores admins. We need to fetch them.
        # This mirrors the legacy behavior but uses the new DB structure.

        admins_cursor = ChatAdminModel.find(ChatAdminModel.chat.id == chat_model.iid)
        admins = await admins_cursor.to_list()

        admin_list_doc = []
        for admin_entry in admins:
            # We need to fetch the user details. ChatAdminModel links to User (ChatModel).
            # admin_entry.user is a Link. We need to fetch it if not automatically fetched (Beanie usually requires fetch).

            # Optimization: If ChatAdminModel definition doesn't auto-fetch, we might need to agg or fetch separately.
            # Assuming standard Beanie Link behavior or fetching explicitly.

            user = await admin_entry.user.fetch()
            if not user:
                continue

            # Skip anonymous admin bot if desired, or keep it. Legacy skipped "anonymous" rights but here we check user ID.
            if user.tid == TELEGRAM_ANONYMOUS_ADMIN_BOT_ID:
                continue

            # Check if anonymous admin
            if admin_entry.member.is_anonymous:
                continue

            admin_list_doc.append(UserLink(user.tid, user.first_name_or_title))

        return await self.event.reply(
            Section(
                VList(*admin_list_doc) if admin_list_doc else _("No visible admins found."),
                title=Template(_("Admins in {chat_name}"), chat_name=self.event.chat.title),
            ).to_html(),
            disable_notification=True,
        )
