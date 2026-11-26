from __future__ import annotations

from datetime import timedelta

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from ass_tg.types import ActionTimeArg, BooleanArg, OrArg, WordArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta
from stfu_tg import Doc, Italic, KeyValue, Section, Spacer, Template, UserLink

from sophie_bot.db.models.antispam import AntispamMode, AntispamPolicy, AntispamSettingsModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.filters.message_status import HasArgs
from sophie_bot.modules.antispam.callbacks import AntispamIncidentCallback, IncidentOperation
from sophie_bot.modules.antispam.provider import AntispamProvider, ProviderOrigin
from sophie_bot.modules.antispam.service import resolve_incident
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log

MODE_LABELS = {
    AntispamMode.OFF: l_("Off"),
    AntispamMode.NORMAL: l_("Normal (Mute)"),
    AntispamMode.STRICT: l_("Strict (Ban)"),
}


def _state(enabled: bool) -> str:
    return _("On") if enabled else _("Off")


def _service_status(provider: AntispamProvider, policy: AntispamPolicy, active: tuple[AntispamProvider, ...]) -> str:
    if provider not in active:
        return _("Disabled")
    if policy.mode == AntispamMode.OFF:
        return _("Off")
    if provider.origin == ProviderOrigin.THIRD_PARTY and not policy.third_party_enabled:
        return _("Off")
    return _("Active")


async def status_doc(chat: ChatModel, services: ApplicationServices, *, locale: str = "en_US") -> Doc:
    policy = await AntispamSettingsModel.get_policy(chat)
    trust_label = (
        format_timedelta(timedelta(seconds=policy.trusted_after_seconds), locale=locale.replace("-", "_"))
        if policy.trusted_after_seconds is not None
        else _("Off")
    )
    doc = Doc(
        Section(
            KeyValue(_("Mode"), str(MODE_LABELS[policy.mode])),
            KeyValue(_("Third-party sources"), _state(policy.third_party_enabled)),
            KeyValue(_("Trust members after"), trust_label),
            title=_("Anti-spam"),
        ),
    )

    registry = services.antispam
    if registry is not None:
        active = await registry.active(chat.tid)
        doc += Section(
            *(KeyValue(provider.display_name, _service_status(provider, policy, active)) for provider in registry),
            title=_("Services"),
        )

    doc += Spacer()
    doc += Template(_("Use {cmd} to set the anti-spam mode."), cmd=Italic("/antispam <off/normal/strict>"))
    doc += Template(_("Use {cmd} to toggle third-party sources."), cmd=Italic("/antispam_3party <on/off>"))
    doc += Template(_("Use {cmd} to trust long-standing members."), cmd=Italic("/antispam_trust <3d/30d/off>"))
    return doc


def _admin_filters() -> tuple[CallbackType, ...]:
    return (
        FeatureFlagFilter("antispam"),
        GroupOrConnectedFilter(),
        UserRestricting(admin=True),
    )


@flags.handler_help(description=l_("Show anti-spam status and active services for this chat."))
class AntispamStatusHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("antispam"), ~HasArgs(True), *_admin_filters()

    async def handle(self) -> None:
        await reply_or_answer(
            self.event, await status_doc(self.connection.db_model, self.services, locale=self.current_locale)
        )


@flags.handler_help(description=l_("Set anti-spam mode for this chat (off, normal, or strict)."))
class AntispamModeHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("antispam"), HasArgs(True), *_admin_filters()

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, object]) -> dict[str, ArgFabric]:
        return {"mode": WordArg(l_("Mode"))}

    async def handle(self) -> None:
        chat = self.connection.db_model
        raw_mode: str = self.data["mode"]
        try:
            mode = AntispamMode(raw_mode.lower())
        except ValueError:
            raise SophieException(_("Choose off, normal or strict.")) from None
        await AntispamSettingsModel.update_policy(chat, lambda policy: policy.model_copy(update={"mode": mode}))
        await reply_or_answer(self.event, await status_doc(chat, self.services, locale=self.current_locale))


@flags.handler_help(description=l_("Turn third-party anti-spam sources on or off for this chat."))
class AntispamThirdPartyHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("antispam_3party"), *_admin_filters()

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, object]) -> dict[str, ArgFabric]:
        return {"enabled": BooleanArg(l_("New state"))}

    async def handle(self) -> None:
        chat = self.connection.db_model
        enabled: bool = self.data["enabled"]
        await AntispamSettingsModel.update_policy(
            chat, lambda policy: policy.model_copy(update={"third_party_enabled": enabled})
        )
        await reply_or_answer(self.event, await status_doc(chat, self.services, locale=self.current_locale))


@flags.handler_help(description=l_("Exempt chat members from anti-spam after a membership duration (or off)."))
class AntispamTrustHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("antispam_trust"), *_admin_filters()

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, object]) -> dict[str, ArgFabric]:
        return {
            "trust": OrArg(
                ActionTimeArg(l_("Duration (e.g., 3d, 30d)"), min=timedelta(minutes=1), max=timedelta(days=3650)),
                WordArg(l_("off")),
            )
        }

    async def handle(self) -> None:
        chat = self.connection.db_model
        trust: timedelta | str = self.data["trust"]
        if isinstance(trust, timedelta):
            seconds: int | None = int(trust.total_seconds())
        elif trust.lower() == "off":
            seconds = None
        else:
            raise SophieException(_("Specify a duration (for example 3d or 30d) or off."))
        await AntispamSettingsModel.update_policy(
            chat, lambda policy: policy.model_copy(update={"trusted_after_seconds": seconds})
        )
        await reply_or_answer(self.event, await status_doc(chat, self.services, locale=self.current_locale))


class AntispamIncidentCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            AntispamIncidentCallback.filter(),
            FeatureFlagFilter("antispam"),
            GroupOrConnectedFilter(),
            UserRestricting(can_restrict_members=True),
        )

    async def handle(self) -> None:
        await self.check_for_message()
        callback: AntispamIncidentCallback = self.callback_data
        chat = self.connection.db_model
        admin = self.event.from_user

        result = await resolve_incident(callback.incident_id, callback.op, chat, admin, self.services)
        if result.status == "not_found":
            await self.event.answer(_("This anti-spam record was not found."), show_alert=True)
            return
        if result.status == "already_resolved":
            await self.event.answer(_("This action has already been handled."), show_alert=True)
            return
        if result.status == "unsupported" or result.incident is None:
            await self.event.answer(_("This action is not available."), show_alert=True)
            return

        incident = result.incident
        user_link = UserLink(incident.user_tid, incident.user_name)
        admin_link = UserLink(admin.id, admin.first_name)
        if callback.op == IncidentOperation.UNDO:
            summary = Template(
                _("Anti-spam action for {user} was undone by {admin}. User added to anti-spam exclusions."),
                user=user_link,
                admin=admin_link,
            )
            toast = _("Action undone and user excluded from anti-spam.")
        else:
            summary = Template(
                _("{user} has been banned by {admin}."),
                user=user_link,
                admin=admin_link,
            )
            toast = _("User banned.")

        doc = Doc(summary)
        if incident.delete_at is not None:
            doc += Italic(_("This message will be automatically deleted in about 10 minutes."))

        message = self.event.message
        if isinstance(message, Message):
            try:
                await message.edit_text(doc.to_html(), reply_markup=None)
            except TelegramAPIError:
                log.debug("Could not edit anti-spam notice after resolution", incident_id=str(incident.id))

        await self.event.answer(toast)
