from typing import Any

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel
from stfu_tg import Doc, KeyValue, Section, Template, Title, UserLink
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.action_config_wizard import ActionWizardSetting, ActionWizardSpec
from sophie_bot.modules.warns.utils import warn_user
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class WarnActionDataModel(BaseModel):
    reason: str | None


async def setup_confirm(event: Message | CallbackQuery, data: dict[str, Any]) -> WarnActionDataModel:
    if isinstance(event, CallbackQuery):
        raise TypeError("This handlers setup_confirm can only be used with messages")

    reason = event.text or None

    return WarnActionDataModel(reason=reason)


async def setup_message(_event: Message | CallbackQuery, _data: dict[str, Any]) -> Element:
    return Template(_("Please write the warn reason."))


def build_action_wizard_specs() -> dict[str, ActionWizardSpec]:
    return {
        WARN_ACTION.name: ActionWizardSpec(
            interactive_setup=None,
            settings=lambda _data: {
                "change_warn_reason": ActionWizardSetting(
                    title=l_("Change warn reason"),
                    icon="📝",
                    setup_message=setup_message,
                    setup_confirm=setup_confirm,
                )
            },
        )
    }


WARN_ACTION = ActionDefinition[WarnActionDataModel](
    name="warn_user",
    icon="⚠️",
    title=l_("Warn"),
    data_object=WarnActionDataModel,
    default_data=WarnActionDataModel(reason=None),
    allow_warns=False,
    skip_for_admins=True,
    has_interactive_setup=False,
)


class WarnModernAction(ModernActionABC[WarnActionDataModel]):
    definition = WARN_ACTION

    @staticmethod
    def description(data: WarnActionDataModel) -> Element | str:
        if data.reason:
            # TODO: not en_US
            return Section(data.reason, title=_("Warn user with the reason"), title_underline=False)

        return _("Warns user with no reason")

    async def handle(self, message: Message, data: dict, filter_data: WarnActionDataModel) -> Element | None:
        if not message.from_user:
            return

        chat_db = data["context"].event_chat
        admin_db = await ChatModel.get_by_tid(CONFIG.bot_id)
        if not admin_db:
            if not message.bot:
                return
            bot_me = await message.bot.get_me()
            admin_db = await ChatModel.upsert_user(bot_me)
        # Channel and unresolved anonymous-admin events may not have a persisted
        # actor. The sender is available after the early return above.
        target_db = data["context"].actor or await ChatModel.upsert_user(message.from_user)

        text = filter_data.reason
        if not text:
            message_text = message.text or message.caption or None
            ai_reason = await generate_restriction_reason(
                chat_db,
                message_text=message_text,
                include_rules=True,
                services=data["services"],
            )
            if ai_reason:
                text = ai_reason

        if not text:
            text = _("No reason")

        current, limit, punishment, _warn = await warn_user(
            chat_db,
            target_db,
            admin_db,
            text,
            trigger_message=message,
            action_context=data,
            services=data["services"],
        )

        if "filter_id" in data:
            await log_event(
                chat_db.tid,
                CONFIG.bot_id,
                LogEvent.WARN_ADDED,
                {
                    "target_user_id": target_db.tid,
                    "reason": text,
                    "current": current,
                    "limit": limit,
                    "filter_id": data["filter_id"],
                    "action": "warn_user",
                },
            )

        doc = Doc(
            Title(_("Filter action")),
            Template(
                _("User {user} was automatically warned based on a filter action"),
                user=UserLink(message.from_user.id, message.from_user.first_name),
            ),
            KeyValue(_("Warnings count"), f"{current}/{limit}"),
        )

        if filter_data.reason:
            doc += Section(filter_data.reason, title=_("Reason"), title_underline=False)

        if punishment:
            doc += Section(Template(_("User has been {punishment} due to reaching max warns."), punishment=punishment))

        return doc
