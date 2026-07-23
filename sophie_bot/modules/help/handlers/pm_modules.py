from typing import Any, Optional

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Doc, Heading, ListItem, Paragraph, RichBlockQuote, Template, UnorderedList, Url

from sophie_bot.config import CONFIG
from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.callbacks import AIChatCallback
from sophie_bot.modules.help.callbacks import (
    PMHelpModule,
    PMHelpModules,
    PMHelpStartUrlCallback,
)
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES, get_aliased_cmds
from sophie_bot.modules.help.utils.format_help import format_handler_item, group_handlers
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Shows help overview for all modules"))
class PMModulesList(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router):
        router.message.register(cls, PMHelpStartUrlCallback.filter(), ChatTypeFilter("private"))
        router.message.register(cls, CMDFilter("help"), ChatTypeFilter("private"))
        router.callback_query.register(cls, PMHelpModules.filter())

    async def handle(self) -> Any:
        callback_data: Optional[PMHelpModules] = self.data.get("callback_data", None)

        # Sort item by the module title
        modules = dict(sorted(HELP_MODULES.items(), key=lambda item: str(item[1].name)))
        # Put the featured module to the bottom; re-assigning an existing key would not move it
        if (featured_module := modules.pop(CONFIG.help_featured_module, None)) is not None:
            modules[CONFIG.help_featured_module] = featured_module

        buttons = InlineKeyboardBuilder()

        buttons.row(
            *(
                InlineKeyboardButton(
                    text=f"{module.icon} {module.name}",
                    callback_data=PMHelpModule(
                        module_name=module_name, back_to_start=bool(callback_data and callback_data.back_to_start)
                    ).pack(),
                )
                for module_name, module in modules.items()
                if not module.exclude_public
            ),
            width=2,
        )

        if callback_data and callback_data.back_to_start:
            buttons.row(InlineKeyboardButton(text=_("⬅️ Back"), callback_data="go_to_start", style="primary"))

        buttons.row(
            InlineKeyboardButton(
                text=str(Template(_("💬{ai_emoji} Chat with Sophie for help"), ai_emoji=AI_EMOJI)),
                callback_data=AIChatCallback().pack(),
                style="primary",
            )
        )

        doc = Doc(
            Heading(_("Help")),
            Paragraph(_("There are three ways to find your way around Sophie:")),
            UnorderedList(
                ListItem(Url(_("📖 The wiki"), CONFIG.wiki_link) + " — " + _("detailed information on every feature")),
                ListItem(_("🧩 The modules below — a quick overview of the commands in each one")),
                ListItem(_("💬 Sophie herself — ask her how to use her, in your own words")),
            ),
        )

        await self.answer_rich(doc, reply_markup=buttons.as_markup())


class PMModuleHelp(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (PMHelpModule.filter(),)

    async def handle(self) -> Any:
        callback_data: PMHelpModule = self.data["callback_data"]
        module_name = callback_data.module_name
        module = HELP_MODULES.get(module_name)

        if not module:
            await self.event.answer(_("Module not found"))
            return

        cmds = list(filter(lambda x: not x.only_op, module.handlers))

        doc = Doc(Heading(f"{module.icon} {module.name}"))
        if module.description:
            doc += RichBlockQuote(module.description)
        if module.info:
            doc += Paragraph(module.info)

        for section_title, handlers in group_handlers(cmds):
            doc += Heading(section_title, level=2)
            doc += UnorderedList(*(ListItem(format_handler_item(handler)) for handler in handlers))

        for a_mod_name, a_cmds in get_aliased_cmds(module_name).items():
            a_module = HELP_MODULES[a_mod_name]
            doc += Heading(
                Template(_("Aliased commands from {module}"), module=f"{a_module.icon} {a_module.name}"), level=2
            )
            doc += UnorderedList(*(ListItem(format_handler_item(handler)) for handler in a_cmds))

        buttons = InlineKeyboardBuilder()

        if module.advertise_wiki_page:
            doc += Paragraph(
                Url(_("📖 Look the module's wiki page for more information"), CONFIG.wiki_modules_link + module_name)
            )
            buttons.row(InlineKeyboardButton(text=_("📖 Wiki page"), url=CONFIG.wiki_modules_link + module_name))

        buttons.row(
            InlineKeyboardButton(
                text=_("⬅️ Back"),
                callback_data=PMHelpModules(back_to_start=callback_data.back_to_start).pack(),
                style="primary",
            )
        )

        await self.answer_rich(doc, reply_markup=buttons.as_markup())
