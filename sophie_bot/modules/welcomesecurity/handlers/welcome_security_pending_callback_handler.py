from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, KeyValue, UserLink

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_back_keyboard import _back_keyboard
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityPendingCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "pending"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, _settings, _message = context
        pending = WSUserModel.find(WSUserModel.group.id == chat.iid)
        total = await pending.count()
        verified = await WSUserModel.find(WSUserModel.group.id == chat.iid, WSUserModel.passed == True).count()
        entries: list[KeyValue] = []
        async for record in WSUserModel.find(WSUserModel.group.id == chat.iid).sort("-added_at").limit(10):
            user = await ChatModel.get_by_iid(record.user.ref.id)
            if user is not None:
                entries.append(
                    KeyValue(
                        UserLink(user.tid, user.first_name_or_title),
                        _("Verified")
                        if record.passed
                        else _("Join request")
                        if record.is_join_request
                        else _("Awaiting CAPTCHA"),
                    )
                )
        await self.event.answer()
        return await self.edit_text(
            Doc(
                KeyValue(_("Awaiting CAPTCHA"), total - verified),
                *entries,
                KeyValue(_("Verified CAPTCHA records"), verified),
                _("Showing up to 10 recent records. Use /unmute <user> for manual permission recovery."),
            ),
            reply_markup=_back_keyboard(chat.iid),
        )
