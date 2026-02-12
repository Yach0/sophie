from sophie_bot.db.models.federations import Federation


class FederationPermissionService:
    """Service for checking federation permissions."""

    @staticmethod
    async def is_federation_owner(federation: Federation, user_tid: int) -> bool:
        """Check if user is the federation owner."""
        creator = await federation.creator.fetch()
        if creator and creator.tid == user_tid:
            return True
        return False

    @staticmethod
    async def is_federation_admin(federation: Federation, user_tid: int) -> bool:
        """Check if user is a federation admin."""
        if await FederationPermissionService.is_federation_owner(federation, user_tid):
            return True

        if federation.admins:
            for admin_link in federation.admins:
                admin = await admin_link.fetch()
                if admin and admin.tid == user_tid:
                    return True
        return False

    @staticmethod
    async def can_manage_federation(federation: Federation, user_tid: int) -> bool:
        """Check if user can manage federation (owner or admin)."""
        return await FederationPermissionService.is_federation_admin(federation, user_tid)

    @staticmethod
    async def can_ban_in_federation(federation: Federation | None, user_tid: int) -> bool:
        """Check if user can ban in federation."""
        if federation is None:
            return False
        return await FederationPermissionService.is_federation_admin(federation, user_tid)

    @staticmethod
    async def validate_federation_owner(federation: Federation, user_tid: int) -> bool:
        """Validate that user is federation owner."""
        return await FederationPermissionService.is_federation_owner(federation, user_tid)

    @staticmethod
    async def validate_federation_admin(federation: Federation, user_tid: int) -> bool:
        """Validate that user is federation admin."""
        return await FederationPermissionService.is_federation_admin(federation, user_tid)
