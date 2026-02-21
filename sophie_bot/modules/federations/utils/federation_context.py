from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from stfu_tg import Doc

from sophie_bot.modules.federations.exceptions import FederationContextError, FederationNotFoundError
from sophie_bot.utils.i18n import gettext as _

if TYPE_CHECKING:
    pass


class FederationContextMixin:
    """Mixin providing federation resolution context for handlers."""

    async def resolve_federation_from_context(
        self,
        fed_id_arg: Optional[str] = None,
        require_permission: bool = False,
        user_tid: Optional[int] = None,
    ) -> tuple[Any, Optional[str]]:
        """
        Resolve federation from command context.

        Args:
            fed_id_arg: Optional federation ID provided by user
            require_permission: Whether to check ban permission
            user_tid: Telegram user ID to check permission for (defaults to event.from_user.id)

        Returns:
            Tuple of (result, error_message) where result is either:
            - Federation object if successful
            - None if an error occurred

        Errors are replied to user and returned as error_message.
        """
        from sophie_bot.modules.federations.services import FederationManageService

        event = getattr(self, "event", None)

        if not event:
            error_msg = "Unable to access event object"
            await getattr(self, "event").reply(error_msg)
            return None, error_msg

        # Get chat and user ID from event
        connection = getattr(self, "connection", None)

        if user_tid is None:
            from_user = getattr(event, "from_user", None)
            user_tid = from_user.id if from_user else None

        # Try to resolve federation
        try:
            federation = await FederationManageService.get_federation(fed_id_arg, connection, user_tid)
        except (FederationNotFoundError, FederationContextError) as e:
            error_msg = str(e)
            await event.reply(error_msg)
            return None, error_msg

        # Check permissions if required
        if require_permission and user_tid:
            from sophie_bot.modules.federations.services.permissions import FederationPermissionService

            if not await FederationPermissionService.can_ban_in_federation(federation, user_tid):
                error_msg = _("You don't have permission to perform this action in this federation.")
                await event.reply(error_msg)
                return None, error_msg

        return federation, None

    async def reply_with_error(
        self,
        error_message: str,
    ) -> None:
        """Reply with error message and return None."""
        doc = Doc(
            _("Error"),
            error_message,
        )
        event = getattr(self, "event", None)
        if event:
            await event.reply(str(doc))
