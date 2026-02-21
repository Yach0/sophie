from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import BufferedInputFile, Message
from ass_tg.types import EqualsArg, OptionalArg
from stfu_tg import Code, Doc, KeyValue, Title, UserLink, VList, Template

from sophie_bot.args.users import SophieUserArg
from sophie_bot.constants import MAX_FCHECK_INLINE_ITEMS
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import IsConnectedFilter
from sophie_bot.modules.federations.services import FederationBanService
from sophie_bot.services.bot import bot
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Check federation bans"))
class FederationCheckPMHandler(SophieMessageHandler):
    """Handler for checking fed bans in private chat when not connected."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("fcheck", "fbanstat")),
            FeatureFlagFilter("new_feds_fcheck"),
            ChatTypeFilter("private"),
            ~IsConnectedFilter(),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "user": OptionalArg(SophieUserArg(l_("User to check"), allow_unknown_id=True)),
            "full": OptionalArg(EqualsArg("full", l_("'full' to show all bans"))),
        }

    async def handle(self) -> Any:
        if self.connection.type != ChatType.private:
            return

        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        target_user: ChatModel | None = self.data.get("user")
        if not target_user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if reply_from_user:
                target_user = await ChatModel.get_by_tid(reply_from_user.id)
                if not target_user:
                    raise ValueError("Target user not found in database")
            else:
                target_user = self.data.get("user_db")

        if not target_user:
            await self.event.reply(_("Please specify a user or reply to a user's message."))
            return

        show_full = self.data.get("full") is not None

        bans = await FederationBanService.get_user_fed_bans(target_user.tid, only_with_banned_chats=not show_full)
        total_bans = await FederationBanService.get_user_fed_bans(target_user.tid, only_with_banned_chats=False)

        if not bans:
            await self.event.reply(
                Doc(
                    Title(_("🏛 Federation Ban Check")),
                    KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title or _("Unknown"))),
                    _("No federation bans found."),
                ).to_html()
            )
            return

        if len(bans) > MAX_FCHECK_INLINE_ITEMS:
            await self._send_csv_export(target_user, bans, show_full)
            return

        items = []
        for ban, federation in bans:
            reason = ban.reason or _("No reason provided")
            fed_text = f"{federation.fed_name} ({federation.fed_id})"
            items.append(KeyValue(fed_text, reason))

        doc = Doc(
            Title(_("🏛 Federation Ban Check")),
            KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title or _("Unknown"))),
            KeyValue(_("Total bans"), Code(str(len(bans)))),
            VList(*items),
        )
        if not show_full and len(total_bans) > len(bans):
            doc += Template(
                _(
                    "Some federation bans are hidden because they do not affect any chats. "
                    "Use {cmd} to show the complete list."
                ),
                cmd=Code("/fcheck full"),
            )
        await self.event.reply(doc.to_html())

    async def _send_csv_export(
        self, target_user: ChatModel, bans: list[tuple[FederationBan, Federation]], show_full: bool
    ) -> None:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["fed_id", "fed_name", "reason", "banned_chats_count"])

        for ban, federation in bans:
            reason = ban.reason or ""
            writer.writerow([federation.fed_id, federation.fed_name, reason, len(ban.banned_chats or [])])

        csv_bytes = output.getvalue().encode("utf-8")
        filename = f"{target_user.tid}_fcheck_{'full' if show_full else 'limited'}.csv"
        document = BufferedInputFile(csv_bytes, filename=filename)

        caption = Doc(
            Title(_("🏛 Federation Ban Check Export")),
            KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title or _("Unknown"))),
            KeyValue(_("Total bans"), Code(str(len(bans)))),
        ).to_html()

        await bot.send_document(chat_id=self.event.chat.id, document=document, caption=caption)
