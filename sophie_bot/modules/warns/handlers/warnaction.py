from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from beanie import PydanticObjectId
from stfu_tg import Doc, Section, Template, VList
from stfu_tg.doc import Element

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.utils_.action_config_wizard.callbacks import ACWCoreCallback
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

DEFAULT_MAX_WARN_ACTION = "ban_user"


class WarnActionRenderer:
    """Renderer for the warnaction view. Can be used standalone or from handlers."""

    @staticmethod
    def format_actions(actions: list) -> Element | str:
        if not actions:
            return _("No actions configured")

        parts: list[Element] = []
        for action in actions:
            action_meta = ALL_MODERN_ACTIONS.get(action.name)
            if not action_meta:
                continue

            description = action_meta.description(convert_action_data_to_model(action_meta, action.data))
            parts.append(
                Template(
                    "{icon} {description}",
                    icon=action_meta.icon,
                    description=description,
                )
            )

        if not parts:
            return _("No actions configured")

        return VList(*parts)

    @staticmethod
    def get_default_max_warn_text() -> Element | str:
        """Get the display text for max warns action (configured or default)."""
        default_action = ALL_MODERN_ACTIONS.get(DEFAULT_MAX_WARN_ACTION)
        if default_action:
            return Template(
                _("{icon} {title} (default)"),
                icon=default_action.icon,
                title=default_action.title,
            )
        return _("Ban the user (default)")

    @staticmethod
    async def render_warnaction_view(chat_iid: PydanticObjectId) -> tuple[str, Any]:
        """Render the warnaction view text and keyboard.

        Returns (text_html, reply_markup)
        """
        settings = await WarnSettingsModel.get_or_create(chat_iid)

        each_warn_text = WarnActionRenderer.format_actions(settings.on_each_warn_actions)

        # Show configured actions or default for max warns
        if settings.on_max_warn_actions:
            max_warn_text = WarnActionRenderer.format_actions(settings.on_max_warn_actions)
        else:
            max_warn_text = WarnActionRenderer.get_default_max_warn_text()

        doc = Doc(
            Section(
                each_warn_text,
                title=_("On each warn"),
            ),
            Section(
                max_warn_text,
                title=_("On exceeding warnings"),
            ),
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("Configure on each warn"),
                callback_data=ACWCoreCallback(mod="warn_action_each", op="show").pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=_("Configure on warnings exceeding"),
                callback_data=ACWCoreCallback(mod="warn_action_max", op="show").pack(),
            )
        )

        return doc.to_html(), builder.as_markup()


@flags.help(description=l_("Configures warn actions."))
class WarnActionHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("warnaction", "warn_action")), UserRestricting(can_restrict_members=True)

    async def handle(self) -> Any:
        chat_iid = self.connection.db_model.iid
        text, markup = await WarnActionRenderer.render_warnaction_view(chat_iid)
        await self.event.reply(text, reply_markup=markup)
