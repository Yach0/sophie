from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message, User
from ass_tg.types import OptionalArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Bold, Doc, Italic, Template, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.db_exceptions import DBNotFoundException
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.chat import ChatType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationManageService, FederationAdminService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Promote a user to federation admin"))
class FederationPromoteHandler(FederationCommandHandler):
    """Handler for promoting users to federation admin."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("fpromote",)), FeatureFlagFilter("new_feds_fpromote")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        """Define arguments for promote command."""
        base_args = await super().handler_args(message, data)

        # Only require user argument if not replying to a message
        if not message or not is_real_reply(message):
            base_args["user"] = OptionalArg(SophieUserArg(l_("User")))

        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Promote a user to federation admin."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        # Get user from reply or argument
        try:
            user_input = get_arg_or_reply_user(self.event, self.data)
        except Exception:
            await self.event.reply(_("Please specify a user to promote or reply to their message."))
            return

        # Convert to ChatModel if necessary
        if isinstance(user_input, User):
            try:
                user_db = await ChatModel.find_user(user_input.id)
            except DBNotFoundException:
                await self.event.reply(_("User not found in database."))
                return
        else:
            user_db = user_input

        # Verify the user is a real user (not a group)
        if user_db.type != ChatType.private:
            await self.event.reply(_("Can only promote individual users to admin."))
            return

        # Check permissions - only federation admins can promote
        if not await FederationPermissionService.validate_federation_admin(federation, self.event.from_user.id):
            await self.event.reply(_("Only federation admins can promote users."))
            return

        # Try to promote the user
        try:
            await FederationAdminService.promote_admin(federation, user_db.iid)
        except ValueError as e:
            if "already an admin" in str(e):
                await self.event.reply(
                    Template(
                        _("{user} is already an admin of this federation."),
                        user=Bold(UserLink(user_db.tid, user_db.first_name_or_title)),
                    ).to_html()
                )
            else:
                await self.event.reply(str(e))
            return

        # Send success message
        await self.event.reply(
            Doc(
                Template(
                    _("{user} has been promoted to admin of federation {fed_name}."),
                    user=Bold(UserLink(user_db.tid, user_db.first_name_or_title)),
                    fed_name=Italic(federation.fed_name),
                ),
            ).to_html()
        )

        # Log the promotion
        log_text = Template(
            _("👤 {admin} promoted {user} to admin in federation {fed_name}."),
            admin=self.event.from_user.mention_html(),
            user=UserLink(user_db.tid, user_db.first_name_or_title),
            fed_name=federation.fed_name,
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)
