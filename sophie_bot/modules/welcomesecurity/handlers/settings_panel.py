from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import MagicData, StateFilter
from aiogram.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from babel.dates import format_timedelta
from beanie import PydanticObjectId
from redis.asyncio import Redis
from stfu_tg import Doc, KeyValue, Section, UserLink

from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel, WSUserModel
from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.greetings.default_welcome import get_default_security_message
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.handlers.status_overall import CAPTCHA_EXPIRY_OPTIONS, _effective_expiry
from sophie_bot.utils import flags
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _

MEDIA_DURATION_OPTIONS = (timedelta(hours=1), timedelta(hours=12), WELCOMEMUTE_DEFAULT_TIME, timedelta(weeks=1))


def _button(chat_iid: PydanticObjectId, text: str, action: str, value: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=WelcomeSecuritySettingsCB(chat_iid=str(chat_iid), action=action, value=value).pack(),
    )


def _back_keyboard(chat_iid: PydanticObjectId) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(chat_iid, _("Back to settings"), "home")]])


def _panel_keyboard(settings: GreetingsModel, chat_iid: PydanticObjectId) -> InlineKeyboardMarkup:
    captcha_enabled = bool(settings.welcome_security and settings.welcome_security.enabled)
    media_enabled = bool(settings.welcome_mute and settings.welcome_mute.enabled and settings.welcome_mute.time)
    cleanup_enabled = bool(settings.clean_service and settings.clean_service.enabled)
    welcome_cleanup_enabled = bool(settings.clean_welcome and settings.clean_welcome.enabled)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    chat_iid,
                    _("Disable CAPTCHA") if captcha_enabled else _("Enable CAPTCHA"),
                    "captcha",
                    int(not captcha_enabled),
                ),
                _button(chat_iid, _("CAPTCHA expiry"), "expiry"),
            ],
            [
                _button(
                    chat_iid,
                    _("Disable media restriction") if media_enabled else _("Enable media restriction"),
                    "media",
                    int(not media_enabled),
                ),
                _button(chat_iid, _("Restriction duration"), "duration"),
            ],
            [
                _button(chat_iid, _("View security message"), "message"),
                _button(chat_iid, _("Edit security message"), "edit"),
            ],
            [_button(chat_iid, _("Reset security message"), "reset_confirm")],
            [
                _button(
                    chat_iid,
                    _("Keep join/leave messages") if cleanup_enabled else _("Delete join/leave messages"),
                    "cleanup",
                    int(not cleanup_enabled),
                )
            ],
            [
                _button(
                    chat_iid,
                    _("Keep previous welcome messages")
                    if welcome_cleanup_enabled
                    else _("Delete previous welcome messages"),
                    "welcome_cleanup",
                    int(not welcome_cleanup_enabled),
                )
            ],
            [_button(chat_iid, _("Pending users"), "pending")],
        ]
    )


async def _panel_text(settings: GreetingsModel, chat_tid: int, locale: str, *, redis: Redis) -> Doc:
    captcha_enabled = bool(settings.welcome_security and settings.welcome_security.enabled)
    service_enabled = await is_enabled("welcomecaptcha", chat_tid=chat_tid, redis=redis)
    expiry_enabled = await is_enabled("welcomecaptcha_autokick", chat_tid=chat_tid, redis=redis)
    captcha_status = _("Enabled") if captcha_enabled and service_enabled else _("Disabled")
    if captcha_enabled and not service_enabled:
        captcha_status = _("Disabled by service configuration")
    media_enabled = bool(settings.welcome_mute and settings.welcome_mute.enabled and settings.welcome_mute.time)
    duration = (settings.welcome_mute.time if settings.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME
    return Doc(
        Section(
            KeyValue(_("CAPTCHA"), captcha_status),
            KeyValue(
                _("CAPTCHA expiry"), format_timedelta(_effective_expiry(settings), locale=locale.replace("-", "_"))
            ),
            KeyValue(
                _("Unanswered CAPTCHA"),
                _("Kick members; decline join requests")
                if expiry_enabled
                else _("Timeout removal disabled by service configuration"),
            ),
            KeyValue(_("Media restriction"), _("Enabled") if media_enabled else _("Disabled")),
            KeyValue(_("Restriction duration"), format_timedelta(duration, locale=locale.replace("-", "_"))),
            KeyValue(_("Security message"), _("Custom") if settings.security_note else _("Default")),
            KeyValue(
                _("Join/leave message cleanup"),
                _("Enabled") if settings.clean_service and settings.clean_service.enabled else _("Disabled"),
            ),
            KeyValue(
                _("Previous welcome message cleanup"),
                _("Enabled") if settings.clean_welcome and settings.clean_welcome.enabled else _("Disabled"),
            ),
            title=_("Welcome Security"),
        ),
        _("Settings changes do not ask verified users to solve CAPTCHA again."),
    )


def _duration_keyboard(
    settings: GreetingsModel, chat_iid: PydanticObjectId, locale: str, *, expiry: bool
) -> InlineKeyboardMarkup:
    options = CAPTCHA_EXPIRY_OPTIONS if expiry else MEDIA_DURATION_OPTIONS
    current = (
        _effective_expiry(settings)
        if expiry
        else (settings.welcome_mute.time if settings.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME
    )
    buttons = InlineKeyboardBuilder()
    for duration in options:
        label = format_timedelta(duration, locale=locale.replace("-", "_"))
        buttons.add(
            _button(
                chat_iid,
                ("✅ " if duration == current else "") + label,
                "set_expiry" if expiry else "set_duration",
                int(duration.total_seconds()),
            )
        )
    buttons.adjust(2)
    buttons.row(_button(chat_iid, _("Back to settings"), "home"))
    return buttons.as_markup()


@flags.handler_help(exclude=True)
@flags.disableable(name="welcomesecurity")
class WelcomeSecurityPanelHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("welcome_security", "welcome_captcha", "enable_welcome_captcha", "welcome_restrict")),
            MagicData(~F.command.args),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel"),
        )

    async def handle(self) -> Any:
        if self.connection.type == "private":
            return await self.event.reply(_("Connect to a group before opening Welcome Security settings."))
        chat = self.connection.db_model
        settings = await GreetingsModel.get_by_chat_iid(chat.iid)
        return await self.event.reply(
            str(await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis)),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


async def _callback_context(
    handler: SophieCallbackQueryHandler,
) -> tuple[ChatModel, GreetingsModel, Message] | None:
    chat = handler.connection.db_model
    if handler.connection.type == "private" or handler.callback_data.chat_iid != str(chat.iid):
        await handler.event.answer(
            _("These settings are no longer active. Open /welcome_security again for the intended chat."),
            show_alert=True,
        )
        return None
    await handler.check_for_message()
    message = handler.event.message
    assert isinstance(message, Message)
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    return chat, settings, message


class WelcomeSecurityCaptchaCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "captcha"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {0, 1}:
            return await self.event.answer(_("This setting is no longer available."), show_alert=True)
        await settings.set_status_welcomesecurity(bool(value))
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityMediaCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "media"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {0, 1}:
            return await self.event.answer(_("This setting is no longer available."), show_alert=True)
        duration = (settings.welcome_mute.time if settings.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME
        await settings.set_status_welcomemute(bool(value), duration)
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityCleanupCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "cleanup"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {0, 1}:
            return await self.event.answer(_("This setting is no longer available."), show_alert=True)
        await settings.set_service_clean_status(bool(value))
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityWelcomeCleanupCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "welcome_cleanup"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {0, 1}:
            return await self.event.answer(_("This setting is no longer available."), show_alert=True)
        await settings.set_clean_welcome_status(bool(value))
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecuritySetExpiryCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "set_expiry"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {int(option.total_seconds()) for option in CAPTCHA_EXPIRY_OPTIONS}:
            return await self.event.answer(_("This duration option is no longer available."), show_alert=True)
        await settings.set_status_welcomesecurity(
            bool(settings.welcome_security and settings.welcome_security.enabled), timedelta(seconds=value)
        )
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecuritySetDurationCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "set_duration"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {int(option.total_seconds()) for option in MEDIA_DURATION_OPTIONS}:
            return await self.event.answer(_("This duration option is no longer available."), show_alert=True)
        await settings.set_status_welcomemute(
            bool(settings.welcome_mute and settings.welcome_mute.enabled), timedelta(seconds=value)
        )
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityResetCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "reset"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        settings.security_note = None
        await settings.save()
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityResetConfirmCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "reset_confirm"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, _settings, _message = context
        await self.event.answer()
        return await self.edit_text(
            _("Reset the security message to the default?"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button(chat.iid, _("Reset"), "reset")],
                    [_button(chat.iid, _("Cancel"), "home")],
                ]
            ),
        )


class WelcomeSecurityExpiryCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "expiry"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        await self.event.answer()
        return await self.edit_text(
            _("Choose CAPTCHA expiry. Members are kicked and join requests are declined after this time."),
            reply_markup=_duration_keyboard(settings, chat.iid, self.current_locale, expiry=True),
        )


class WelcomeSecurityDurationCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "duration"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        await self.event.answer()
        return await self.edit_text(
            _("Choose how long new members cannot send media."),
            reply_markup=_duration_keyboard(settings, chat.iid, self.current_locale, expiry=False),
        )


class WelcomeSecurityMessageCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "message"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, message = context
        await self.event.answer()
        rules = await RulesModel.get_rules(chat.iid)
        return await send_saveable(
            message,
            message.chat.id,
            settings.security_note or get_default_security_message(),
            additional_fillings={"rules": rules.text or "" if rules else _("No chat rules, have fun!")},
            connection=self.connection,
            owner_chat_tid=chat.tid,
            bot=self.services.bot,
        )


class WelcomeSecurityEditCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "edit"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, _settings, message = context
        if await self.state.get_state() is not None:
            return await self.event.answer(_("Finish your current action or use /cancel first."), show_alert=True)
        prompt = await message.answer(
            _(
                "Reply to this message with the new security message, or use /cancel. You can include supported media and buttons."
            ),
            reply_markup=ForceReply(selective=True),
        )
        await self.state.set_state(WelcomeSecurityFSM.security_message)
        await self.state.update_data(ws_settings_chat_iid=str(chat.iid), ws_settings_prompt_id=prompt.message_id)
        return await self.event.answer()


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


class WelcomeSecurityHomeCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "home"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        await self.event.answer(None)
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )


class WelcomeSecurityMessageInputHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            StateFilter(WelcomeSecurityFSM.security_message),
            F.reply_to_message,
            ~F.text.startswith("/"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel"),
        )

    async def handle(self) -> Any:
        data = await self.state.get_data()
        chat = self.connection.db_model
        if self.connection.type == "private" or data["ws_settings_chat_iid"] != str(chat.iid):
            await self.state.clear()
            return await self.event.reply(
                _("These settings are no longer active. Open /welcome_security again for the intended chat.")
            )
        if not self.event.reply_to_message or self.event.reply_to_message.message_id != data["ws_settings_prompt_id"]:
            return await self.event.reply(_("Reply to the security-message prompt, or use /cancel."))
        saveable = await parse_saveable(
            self.event,
            None if self.event.rich_message else self.event.text or self.event.caption,
            allow_reply_message=False,
            album=self.data.get("album"),
            bot=self.services.bot,
        )
        if not (saveable.text or saveable.file or saveable.files or saveable.rich_message):
            return await self.event.reply(_("Send text or supported media for the security message."))
        await GreetingsModel.change_security_message(chat.iid, saveable)
        await self.state.clear()
        settings = await GreetingsModel.get_by_chat_iid(chat.iid)
        return await self.event.reply(
            str(await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis)),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )
