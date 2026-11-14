from __future__ import annotations

from collections.abc import Mapping

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import BooleanArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Bold, Button, Doc, Italic, Paragraph, RichTable, RichTableCell, Title

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.message_status import HasArgs
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback, AIModeratorEnabledCallback
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.moderation.categories import (
    MODERATION_CATEGORIES_TITLES,
    ModerationCategory,
)
from sophie_bot.modules.ai.utils.moderation.settings import (
    get_enabled,
    get_levels,
    get_moderator_settings,
    next_level,
    set_category_level,
)
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

LEVEL_TITLES: Mapping[DetectionLevel, LazyProxy] = {
    DetectionLevel.OFF: l_("🚫 Off"),
    DetectionLevel.LOW: l_("🟢 Low"),
    DetectionLevel.NORMAL: l_("🟡 Medium"),
    DetectionLevel.HIGH: l_("🔴 High"),
}


CATEGORY_DESCRIPTIONS: Mapping[ModerationCategory, LazyProxy] = {
    ModerationCategory.SEXUAL: l_("Explicit sexual content and services."),
    ModerationCategory.HATE_AND_DISCRIMINATION: l_("Prejudice and discrimination."),
    ModerationCategory.VIOLENCE_AND_THREATS: l_("Threats and glorified violence."),
    ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT: l_("Crime and hazardous instructions."),
    ModerationCategory.SELFHARM: l_("Suicide and deliberate self-injury."),
    ModerationCategory.HEALTH: l_("Tailored medical advice."),
    ModerationCategory.FINANCIAL: l_("Tailored financial advice."),
    ModerationCategory.LAW: l_("Tailored legal advice."),
    ModerationCategory.PII: l_("Requests for or sharing of personal data."),
}


def _build_doc(settings: AIModeratorModel | None, enabled: bool) -> Doc:
    levels = get_levels(settings)
    return Doc(
        Title(f"{AI_EMOJI} {_('AI Moderator')}"),
        _("Sophie deletes messages that cross a category's detection level."),
        RichTable(
            [RichTableCell(_("Setting"), is_header=True), RichTableCell(_("Value"), is_header=True)],
            [
                RichTableCell(Bold(_("Enabled")), valign="top"),
                RichTableCell(
                    Button(_("✅ On") if enabled else _("🚫 Off"), callback_data=AIModeratorEnabledCallback().pack()),
                    valign="top",
                ),
            ],
            *(
                [
                    RichTableCell(
                        Doc(
                            Bold(str(MODERATION_CATEGORIES_TITLES[category])),
                            Paragraph(str(CATEGORY_DESCRIPTIONS[category])),
                        ),
                        valign="top",
                    ),
                    RichTableCell(
                        Button(
                            str(LEVEL_TITLES[levels[category]]),
                            callback_data=AIModeratorCategoryCallback(category=category.value).pack(),
                        ),
                        valign="top",
                    ),
                ]
                for category in ModerationCategory
            ),
            compact=True,
            striped=True,
        ),
        Italic(_("Tap a value to change it. Category levels cycle through Off, Low, Medium and High.")),
    )


async def _picker_doc(chat: ChatModel) -> Doc:
    settings = await get_moderator_settings(chat.iid)
    mode = await get_chat_mode(chat.iid, AIMode.disabled)
    return _build_doc(settings, get_enabled(settings, mode))


@flags.handler_help(description=l_("Tune what the AI moderator detects in this chat."))
class AIModeratorSetting(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("ai_moderator"),
            UserRestricting(admin=True),
            FeatureFlagFilter("ai_moderation"),
            ~HasArgs(True),
        )

    async def handle(self) -> None:
        await self.answer_rich(await _picker_doc(self.connection.db_model))


@flags.args(new_state=BooleanArg(l_("New state")))
@flags.handler_help(description=l_("Enable or disable the AI moderator in this chat."))
class AIModeratorControl(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("ai_moderator"), UserRestricting(admin=True), FeatureFlagFilter("ai_moderation"), HasArgs(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, object]) -> dict[str, ArgFabric]:
        return {"new_state": BooleanArg(l_("New state"))}

    async def handle(self) -> None:
        new_state: bool = self.data["new_state"]
        await AIModeratorModel.set_state(self.connection.db_model, new_state)
        await self.answer_rich(await _picker_doc(self.connection.db_model))


async def _can_change(handler: SophieCallbackQueryHandler) -> bool:
    await handler.check_for_message()
    message = handler.event.message
    if not isinstance(message, Message):
        await handler.event.answer(_("Invalid message type"))
        return False
    if not await is_user_admin(handler.connection.db_model.tid, handler.event.from_user.id):
        await handler.event.answer(_("You are not allowed to change this setting"))
        return False
    return True


class AIModeratorCategoryToggle(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIModeratorCategoryCallback.filter(), FeatureFlagFilter("ai_moderation")

    async def handle(self) -> None:
        if not await _can_change(self):
            return
        category = ModerationCategory(self.callback_data.category)
        chat = self.connection.db_model
        levels = get_levels(await get_moderator_settings(chat.iid))
        level = next_level(levels[category])
        await set_category_level(chat, category, level)
        await self.answer_rich(await _picker_doc(chat))
        await self.event.answer(f"{MODERATION_CATEGORIES_TITLES[category]}: {LEVEL_TITLES[level]}")


class AIModeratorEnabledToggle(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIModeratorEnabledCallback.filter(), FeatureFlagFilter("ai_moderation")

    async def handle(self) -> None:
        if not await _can_change(self):
            return
        chat = self.connection.db_model
        settings = await get_moderator_settings(chat.iid)
        mode = await get_chat_mode(chat.iid, AIMode.disabled)
        enabled = not get_enabled(settings, mode)
        await AIModeratorModel.set_state(chat, enabled)
        await self.answer_rich(await _picker_doc(chat))
        await self.event.answer(_("AI moderator enabled") if enabled else _("AI moderator disabled"))
