from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from aiogram import Bot
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from ass_tg.types import EqualsArg, OptionalArg
from stfu_tg import Code, KeyValue, Section, UserLink, VList

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.utils_.admin import check_user_admin_permissions
from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit_rich
from sophie_bot.modules.whitelist.callbacks import WhitelistPageCallback, WhitelistRemoveCallback
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.pagination import build_pagination_row, paginate

WHITELIST_PAGE_SIZE = 8


@flags.args(csv_export=OptionalArg(EqualsArg("^csv", l_("Export as CSV with ^csv"))))
@flags.help(description=l_("List users in this group's whitelist, or export them with ^csv."))
class WhitelistedUsersHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("whitelisted"),
            FeatureFlagFilter("group_user_whitelist"),
            ChatTypeFilter("group", "supergroup"),
        )

    async def handle(self) -> Any:
        entries = await get_group_whitelist_entries(self.event.chat.id)
        if self.data.get("csv_export") is not None:
            await send_whitelist_csv(self.event, entries, bot=self.services.bot)
            return
        await render_whitelist_page(self.event, entries, 0, bot=self.services.bot)


async def get_group_whitelist_entries(chat_tid: int) -> list[GroupUserWhitelistModel]:
    return (
        await GroupUserWhitelistModel.find(GroupUserWhitelistModel.chat_tid == chat_tid)
        .sort("+added_at", "+user_tid")
        .to_list()
    )


async def render_whitelist_page(
    event: Message | CallbackQuery,
    entries: list[GroupUserWhitelistModel],
    requested_page: int,
    *,
    bot: Bot,
) -> None:
    if not entries:
        await reply_or_edit_rich(
            event,
            Section(_("No users are whitelisted in this group.")),
            bot=bot,
            reply_markup=None,
        )
        return

    page = paginate(entries, WHITELIST_PAGE_SIZE, requested_page)
    users = [await ChatModel.get_by_tid(entry.user_tid) for entry in page.items]
    user_rows = VList(
        *(
            UserLink(entry.user_tid, user.first_name_or_title if user else _("Unknown user"))
            for entry, user in zip(page.items, users, strict=True)
        )
    )
    document = Section(
        user_rows,
        KeyValue(_("Page"), Code(f"{page.page + 1}/{page.total_pages}")),
        title=_("Users whitelisted in this group"),
    )

    actor = event.from_user
    if isinstance(event, Message):
        chat_tid = event.chat.id
    else:
        callback_message = event.message
        if callback_message is None:
            return
        chat_tid = callback_message.chat.id
    can_remove = (
        actor is not None and (await check_user_admin_permissions(chat_tid, actor.id, ["can_restrict_members"])) is True
    )
    button_rows = (
        [
            [
                InlineKeyboardButton(
                    text=_("Remove"),
                    callback_data=WhitelistRemoveCallback(user_tid=entry.user_tid, page=page.page).pack(),
                )
            ]
            for entry in page.items
        ]
        if can_remove
        else []
    )
    navigation = build_pagination_row(
        page,
        lambda page_number: WhitelistPageCallback(page=page_number).pack(),
    )
    if navigation:
        button_rows.append(navigation)
    markup = InlineKeyboardMarkup(inline_keyboard=button_rows) if button_rows else None
    await reply_or_edit_rich(event, document, bot=bot, reply_markup=markup)


async def send_whitelist_csv(message: Message, entries: list[GroupUserWhitelistModel], *, bot: Bot) -> None:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["chat_id", "user_id", "display_name", "username", "added_at"])
    for entry in entries:
        user = await ChatModel.get_by_tid(entry.user_tid)
        writer.writerow(
            [
                entry.chat_tid,
                entry.user_tid,
                user.first_name_or_title if user else "",
                user.username if user and user.username else "",
                entry.added_at.isoformat(),
            ]
        )

    filename = f"group-whitelist-{message.chat.id}.csv"
    document = BufferedInputFile(output.getvalue().encode("utf-8"), filename=filename)
    caption = Section(
        KeyValue(_("Users"), Code(str(len(entries)))),
        title=_("Group whitelist CSV export"),
    ).to_html()
    await bot.send_document(chat_id=message.chat.id, document=document, caption=caption)
