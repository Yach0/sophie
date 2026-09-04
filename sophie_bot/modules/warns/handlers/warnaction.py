from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from beanie import PydanticObjectId
from stfu_tg import Button, ButtonRow, Buttons, Doc, Section, Template, VList
from stfu_tg.doc import Element

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.utils_.wizard import WizardCallback
from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS
from sophie_bot.utils import flags
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

            description = action_meta.description(action_meta.load_data(action.data))
            parts.append(Template("{icon} {description}", icon=action_meta.icon, description=description))

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
    async def render_warnaction_view(chat_iid: PydanticObjectId) -> tuple[Doc, None]:
        """Render the warning action view with embedded rich buttons."""
        settings = await WarnSettingsModel.get_or_create(chat_iid)
        each_warn_text = WarnActionRenderer.format_actions(settings.on_each_warn_actions)
        max_warn_text = (
            WarnActionRenderer.format_actions(settings.on_max_warn_actions)
            if settings.on_max_warn_actions
            else WarnActionRenderer.get_default_max_warn_text()
        )
        doc = Doc(
            Section(each_warn_text, title=_("On each warn")),
            Section(max_warn_text, title=_("On exceeding warnings")),
            Buttons(
                ButtonRow(
                    Button(
                        _("Configure on each warn"),
                        callback_data=WizardCallback(scope="warn_action_each", op="open").pack(),
                    )
                ),
                ButtonRow(
                    Button(
                        _("Configure on warnings exceeding"),
                        callback_data=WizardCallback(scope="warn_action_max", op="open").pack(),
                    )
                ),
            ),
        )
        return doc, None


@flags.help(description=l_("Configures warn actions."))
class WarnActionHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("warnaction", "warn_action")),
            FeatureFlagFilter("action_config_wizard"),
            UserRestricting(can_restrict_members=True),
        )

    async def handle(self) -> Any:
        chat_iid = self.connection.db_model.iid
        document, markup = await WarnActionRenderer.render_warnaction_view(chat_iid)
        await self.answer_rich(document, reply_markup=markup)
