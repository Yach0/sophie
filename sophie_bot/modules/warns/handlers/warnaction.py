from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Doc, KeyValue, Section, Template

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.utils_.action_config_wizard.callbacks import ACWCoreCallback
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Configures warn actions."))
class WarnActionHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("warnaction", "warn_action")), UserRestricting(can_restrict_members=True)

    @staticmethod
    def _format_actions(actions: list) -> str:
        if not actions:
            return _("No actions configured")

        parts: list[str] = []
        for action in actions:
            action_meta = ALL_MODERN_ACTIONS.get(action.name)
            if not action_meta:
                continue

            description = action_meta.description(convert_action_data_to_model(action_meta, action.data))
            parts.append(
                Template(
                    "{icon} {title}: {description}",
                    icon=action_meta.icon,
                    title=action_meta.title,
                    description=description,
                ).to_html()
            )

        if not parts:
            return _("No actions configured")

        return "\n".join(parts)

    async def handle(self) -> Any:
        chat_iid = self.connection.db_model.iid
        settings = await WarnSettingsModel.get_or_create(chat_iid)

        each_warn_text = self._format_actions(settings.on_each_warn_actions)
        max_warn_text = self._format_actions(settings.on_max_warn_actions)

        doc = Doc(
            Section(
                KeyValue(_("On each warn"), each_warn_text),
                title=_("⚙️ Warn Actions"),
            ),
            Section(
                KeyValue(_("On max warns"), max_warn_text),
                Template(_("Default if empty: {action}"), action=_("Ban")),
            ),
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("📝 Configure on each warn"),
                callback_data=ACWCoreCallback(mod="warn_action_each", op="back").pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=_("🧱 Configure on max warns"),
                callback_data=ACWCoreCallback(mod="warn_action_max", op="back").pack(),
            )
        )

        await self.event.reply(doc.to_html(), reply_markup=builder.as_markup())
