from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import BlockQuote, Code, Doc, KeyValue, Title, VList

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.locks.handlers.lockable import SUPPORTED_LANGUAGES
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Shows all supported languages for locking"))
@flags.disableable(name="locklanguages")
class ListLockLanguagesHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("locklanguages", "locklangs")),)

    async def handle(self) -> Any:
        message: Message = self.event
        items = []
        for lang_code, lang_name in sorted(SUPPORTED_LANGUAGES.items()):
            items.append(KeyValue(Code(f"language:{lang_code}"), lang_name))

        doc = Doc(
            Title(_("Supported languages for locking")),
            BlockQuote(VList(*items), expandable=True),
            _("Use /lock language:code to block messages in that language."),
        )
        await message.reply(doc.to_html())
