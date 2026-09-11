from __future__ import annotations

from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation


class FederationAdminService:
    """Admin operations for federations."""

    @staticmethod
    async def promote_admin(federation: Federation, user_iid: PydanticObjectId) -> None:
        for admin_link in federation.admins:
            if admin_link.to_ref().id == user_iid:
                raise ValueError("User is already an admin")
        user = await ChatModel.get_by_iid(user_iid)
        if user is None:
            raise ValueError("User does not exist")
        federation.admins.append(user)
        await federation.save()

    @staticmethod
    async def demote_admin(federation: Federation, user_iid: PydanticObjectId) -> None:
        admin_count = len(federation.admins)
        federation.admins = [admin for admin in federation.admins if admin.to_ref().id != user_iid]
        if len(federation.admins) == admin_count:
            raise ValueError("User is not an admin")
        await federation.save()

    @staticmethod
    async def is_admin(federation: Federation, user_tid: int) -> bool:
        creator = await ChatModel.get_by_iid(federation.creator.to_ref().id)
        if creator and creator.tid == user_tid:
            return True
        for admin_link in federation.admins:
            admin = await ChatModel.get_by_iid(admin_link.to_ref().id)
            if admin and admin.tid == user_tid:
                return True
        return False
