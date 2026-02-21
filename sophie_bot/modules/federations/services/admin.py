from __future__ import annotations

from beanie import Link, PydanticObjectId
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation


class FederationAdminService:
    """Admin operations for federations."""

    @staticmethod
    async def promote_admin(federation: Federation, user_iid: PydanticObjectId) -> None:
        for admin_link in federation.admins:
            if admin_link.to_ref().id == user_iid:
                raise ValueError("User is already an admin")
        db_ref = DBRef("chats", user_iid)
        federation.admins.append(Link(db_ref, ChatModel))  # type: ignore[arg-type]
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
        creator = await federation.creator.fetch()
        if creator and creator.tid == user_tid:
            return True
        for admin_link in federation.admins:
            admin = await admin_link.fetch()
            if admin and admin.tid == user_tid:
                return True
        return False
