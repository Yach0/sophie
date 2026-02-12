from __future__ import annotations

from typing import Any, ClassVar

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Bold, Doc, Italic, KeyValue, Section, Template, Spacer

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import LazyProxy, gettext as _, ngettext as pl_, lazy_gettext as l_


@flags.help(description=l_("Shows antiflood protection settings"))
class AntifloodInfoHandler(SophieMessageHandler):
    """Handler for showing antiflood information."""

    bool_status: ClassVar[dict[bool, LazyProxy]] = {True: l_("Yes"), False: l_("No")}

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("antiflood", "flood")),
            UserRestricting(admin=True),
            FeatureFlagFilter("antiflood"),
        )

    async def handle(self) -> Any:
        connection = self.connection
        chat_iid = connection.db_model.iid

        model = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)

        if not model:
            doc = Doc(
                Section(
                    KeyValue(_("Antiflood"), self.bool_status[False]),
                    KeyValue(_("Messages limit"), _("Not configured")),
                    KeyValue(_("Time limit"), _("30 seconds")),
                    KeyValue(_("Actions"), _("Mute for 30 minutes (default)")),
                    title=_("📈 Antiflood Settings"),
                ),
                Spacer(),
                Template(_("Use {cmd} to enable antiflood protection"), cmd=Italic("/enableantiflood <on/off>")),
                Template(_("Use {cmd} to set message count threshold"), cmd=Italic("/antiflood_count <count>")),
                Template(_("Use {cmd} to configure actions"), cmd=Italic("/antiflood_action")),
            )
            return await self.event.reply(str(doc))

        # Build actions text with plural support
        actions = model.actions or []
        if actions:
            actions_list = []
            for action in actions:
                action_meta = ALL_MODERN_ACTIONS.get(action.name)
                if not action_meta:
                    continue

                action_text = (
                    action_meta.description(convert_action_data_to_model(action_meta, action.data))
                    if action.data
                    else action.name
                )
                actions_list.append(f"{action_meta.icon} {action_meta.title}: {action_text}")

            actions_text = "\n".join(actions_list)
            actions_header = pl_("Action", "Actions", len(actions))
        else:
            actions_text = _("Mute for 30 minutes (default)")
            actions_header = _("Actions")

        doc = Doc(
            Section(
                KeyValue(_("Antiflood"), self.bool_status[model.enabled]),
                KeyValue(_("Messages limit"), Bold(str(model.message_count))),
                KeyValue(_("Time limit"), _("30 seconds")),
                KeyValue(actions_header, actions_text),
                title=_("📈 Antiflood Settings"),
            ),
            Spacer(),
            Template(_("Use {cmd} to enable antiflood protection"), cmd=Italic("/enableantiflood <on/off>")),
            Template(_("Use {cmd} to set message count threshold"), cmd=Italic("/antiflood_count <count>")),
            Template(_("Use {cmd} to configure actions"), cmd=Italic("/antiflood_action")),
        )
        await self.event.reply(str(doc))
