from __future__ import annotations

from datetime import timedelta

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import ActionTimeArg, OptionalArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta
from stfu_tg import Code, Doc, Template, Title

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.handler_help(description=l_("Shows / changes how long each warning lasts."))
class WarnExpirationHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("warn_expiration"),
            FeatureFlagFilter("warn_expiration"),
            UserRestricting(admin=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {
            "duration": OptionalArg(
                ActionTimeArg(
                    l_("Warning lifetime (e.g., 2h, 7d, 2w)"),
                    min=timedelta(minutes=1),
                    max=timedelta(days=3650),
                )
            )
        }

    async def handle(self) -> None:
        settings = await WarnSettingsModel.get_or_create(self.connection.db_model.iid)
        duration: timedelta | None = self.data.get("duration")
        if duration is not None:
            settings.expiration_seconds = int(duration.total_seconds())
            await settings.save()

        doc = Doc(Title(_("Warning expiration")))
        if settings.expiration_seconds is None:
            doc += _("Warning expiration is disabled.")
        else:
            doc += Template(
                _("Each warning expires after {duration} from its creation time."),
                duration=format_timedelta(
                    timedelta(seconds=settings.expiration_seconds), locale=self.current_locale.replace("-", "_")
                ),
            )
        doc += Template(
            _("Use {configure} to set a lifetime or {disable} to disable expiration."),
            configure=Code("/warn_expiration 7d"),
            disable=Code("/disable_warn_expiration"),
        )
        await self.event.reply(doc.to_html())


@flags.handler_help(description=l_("Disables warning expiration in this chat."))
class DisableWarnExpirationHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("disable_warn_expiration"),
            FeatureFlagFilter("warn_expiration"),
            UserRestricting(admin=True),
        )

    async def handle(self) -> None:
        settings = await WarnSettingsModel.get_or_create(self.connection.db_model.iid)
        settings.expiration_seconds = None
        await settings.save()
        await self.event.reply(Doc(_("Warning expiration is disabled.")).to_html())
