from beanie.odm.fields import Link as BeanieLink

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation


class FederationPermissionService:
    @staticmethod
    async def _resolve_link(link: BeanieLink) -> ChatModel | None:
        resolved = await link.fetch()
        if isinstance(resolved, BeanieLink):
            return None
        return resolved

    @staticmethod
    async def is_federation_owner(federation: Federation, user_tid: int) -> bool:
        creator = await FederationPermissionService._resolve_link(federation.creator)
        if creator and creator.tid == user_tid:
            return True
        return False

    @staticmethod
    async def is_federation_admin(federation: Federation, user_tid: int) -> bool:
        if await FederationPermissionService.is_federation_owner(federation, user_tid):
            return True

        if federation.admins:
            for admin_link in federation.admins:
                admin = await FederationPermissionService._resolve_link(admin_link)
                if admin and admin.tid == user_tid:
                    return True
        return False

    @staticmethod
    async def can_manage_federation(federation: Federation, user_tid: int) -> bool:
        return await FederationPermissionService.is_federation_admin(federation, user_tid)

    @staticmethod
    async def can_ban_in_federation(federation: Federation | None, user_tid: int) -> bool:
        if federation is None:
            return False
        return await FederationPermissionService.is_federation_admin(federation, user_tid)

    @staticmethod
    async def validate_federation_owner(federation: Federation, user_tid: int) -> bool:
        return await FederationPermissionService.is_federation_owner(federation, user_tid)

    @staticmethod
    async def validate_federation_admin(federation: Federation, user_tid: int) -> bool:
        return await FederationPermissionService.is_federation_admin(federation, user_tid)
