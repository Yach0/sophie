from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Doc, Title, Template, Code

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.exceptions import (
    FederationAlreadyExistsError,
    FederationLimitExceededError,
    FederationValidationError,
)
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Create a new federation"))
class CreateFederationHandler(SophieMessageHandler):
    """Handler for creating new federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("newfed", "fnew")), FeatureFlagFilter("new_feds_newfed")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"name": TextArg(l_("Federation name"))}

    async def handle(self) -> Any:
        """Create a new federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        name: str = self.data["name"]

        try:
            # Create federation
            user_db = self.data["user_db"]
            federation = await FederationManageService.create_federation(name, user_db.iid)
        except FederationValidationError as e:
            await self.event.reply(str(e))
            return
        except FederationLimitExceededError:
            await self.event.reply(_("You have reached the maximum number of federations you can create."))
            return
        except FederationAlreadyExistsError:
            await self.event.reply(_("A federation with this name already exists."))
            return

        # Format success message
        doc = Doc(
            Title(_("🏛 Federation Created")),
            Template(_("Federation '{name}' has been created successfully!"), name=federation.fed_name),
            Template(_("Federation ID: {fed_id}"), fed_id=Code(federation.fed_id)),
            Template(_("Use {cmd} to join this federation."), cmd=f"/joinfed {federation.fed_id}"),
            _("You are the owner of this federation."),
        )

        await self.event.reply(str(doc))

        # Log the federation creation
        log_text = Template(
            _("🏛 Federation '{name}' has been created by {user}."),
            name=federation.fed_name,
            user=self.event.from_user.mention_html(),
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)
