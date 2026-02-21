from __future__ import annotations

from abc import ABCMeta
from typing import Any

from aiogram.types import Message
from ass_tg.types import OptionalArg
from stfu_tg import Template, Italic

from sophie_bot.db.models import Federation
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.exceptions import (
    FederationContextError,
    FederationNotFoundError,
)
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class FederationCommandHandler(SophieMessageHandler, metaclass=ABCMeta):
    """Base handler for federation commands with optional fed_id argument.

    This handler provides common functionality for all federation-related commands
    that accept an optional federation ID. If no federation ID is provided, it
    automatically resolves the federation from the current chat context or
    connection context.

    Usage:
        Inherit from this class and implement the `handle_federation_command` method.
        The `federation` parameter will be provided if resolution succeeds.

    Example:
        class MyFedHandler(FederationCommandHandler):
            @staticmethod
            def filters():
                return CMDFilter(("mycommand",))

            async def handle_federation_command(self, federation: Federation) -> None:
                # Your handler logic here
                await self.event.reply(f"Federation: {federation.fed_name}")
    """

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        """Define default arguments for federation commands.

        Includes an optional fed_id argument that falls back to current chat's
        federation when not provided.
        """
        return {
            "fed_id": OptionalArg(FedIdArg(l_("?Federation ID"))),
        }

    async def handle(self) -> Any:
        """Main entry point - resolves federation and calls handle_federation_command."""
        federation, error_message = await self.resolve_federation()
        if error_message or federation is None:
            return None

        return await self.handle_federation_command(federation)

    async def resolve_federation(self) -> tuple[Federation | None, str | None]:
        """Resolve federation from argument or connection context.

        Returns:
            Tuple of (federation, error_message). If federation is None,
            error_message contains the error to display to the user.
        """
        federation: Federation | None = self.data.get("fed_id")

        if federation:
            return federation, None

        # Try to get federation from connection context
        try:
            connection = getattr(self, "connection", None)

            # Check if we have a valid connection
            if not connection:
                error_msg = _("Unable to determine chat context.")
                await self.event.reply(error_msg)
                return None, error_msg

            # For private chats without explicit fed_id, we can't auto-resolve
            if connection.type == ChatType.private and not connection.is_connected:
                error_msg = _("Please specify a federation ID or use this command in a group chat.")
                await self.event.reply(error_msg)
                return None, error_msg

            # Try to get federation for current chat
            chat_iid = connection.db_model.iid
            federation = await FederationManageService.get_federation_for_chat(chat_iid)

            if not federation:
                command = getattr(self, "command", "/command")
                error_msg = Template(
                    _("This chat is not in any federation. Use {cmd} to specify federation."),
                    cmd=Italic(f"{command} <fed_id>"),
                )
                await self.event.reply(error_msg.to_html())
                return None, error_msg.to_html()

            return federation, None

        except (FederationNotFoundError, FederationContextError) as e:
            await self.event.reply(str(e))
            return None, str(e)

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Override this method to implement the handler logic.

        Args:
            federation: The resolved federation object

        Returns:
            Handler result
        """
        raise NotImplementedError("Subclasses must implement handle_federation_command()")

    async def require_owner(self, federation: Federation) -> bool:
        """Check if current user is owner and reply if not."""
        if not self.event.from_user:
            return False
        from sophie_bot.modules.federations.services.permissions import FederationPermissionService

        if not await FederationPermissionService.is_federation_owner(federation, self.event.from_user.id):
            await self.event.reply(_("Only federation owners can perform this action."))
            return False
        return True

    async def require_admin(self, federation: Federation) -> bool:
        """Check if current user is admin and reply if not."""
        if not self.event.from_user:
            return False
        from sophie_bot.modules.federations.services.permissions import FederationPermissionService

        if not await FederationPermissionService.is_federation_admin(federation, self.event.from_user.id):
            await self.event.reply(_("Only federation admins can perform this action."))
            return False
        return True
