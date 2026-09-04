from re import search
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import CommandStart
from stfu_tg import Bold, Section, Title

from sophie_bot.db.models import ChatModel, RulesModel
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_RULES_BUTTON_PATTERN, LEGACY_RULES_BUTTON_PREFIX
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


class LegacyRulesButton(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CommandStart(deep_link=True, magic=F.args.regexp(f"{LEGACY_RULES_BUTTON_PREFIX}_")),)

    async def handle(self) -> Any:
        regex = search(LEGACY_RULES_BUTTON_PATTERN, self.event.text)
        if not regex:
            return

        chat_tid = int(regex.group(1))

        chat = await ChatModel.get_by_tid(chat_tid)
        rules = await RulesModel.get_rules(chat.iid) if chat else None

        if not rules:
            return await self.event.reply(
                str(Section(_("No rules are set for this chat."), title=_("Rules button failed")))
            )

        title = Bold(Title(f"🪧 {_('Rules')}"))

        await send_saveable(
            self.event,
            self.event.chat.id,
            rules,
            title=title,
            reply_to=self.event.message_id,
            connection=self.connection,
            owner_chat_tid=chat.tid,
        )
