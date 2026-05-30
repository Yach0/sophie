from __future__ import annotations

from aiogram.types import ResultChatMemberUnion
from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import NotIn

from sophie_bot.db.models import ChatAdminModel, ChatModel
from sophie_bot.services.bot import bot
from sophie_bot.utils.logger import log


async def save_chat_member(
    chat_iid: PydanticObjectId, user_iid: PydanticObjectId, member: ResultChatMemberUnion
) -> None:
    log.debug("user_details: updating chat member", member_id=member.user.id)
    await ChatAdminModel.upsert_admin(chat_iid, user_iid, member)


async def get_chat_members(chat_tid: int) -> list[ResultChatMemberUnion]:
    return await bot.get_chat_administrators(chat_tid)


async def update_chat_members(chat: ChatModel) -> None:
    chat_members = await get_chat_members(chat.tid)
    current_user_iids: set[PydanticObjectId] = set()

    for member in chat_members:
        user = await ChatModel.get_by_tid(member.user.id)
        if not user:
            log.debug("user_details: user not found in database", user_id=member.user.id)
            continue

        current_user_iids.add(user.iid)
        await save_chat_member(chat.iid, user.iid, member)

    if current_user_iids:
        await ChatAdminModel.find(
            ChatAdminModel.chat.id == chat.iid,
            NotIn(ChatAdminModel.user.id, current_user_iids),
        ).delete()
    else:
        await ChatAdminModel.find(ChatAdminModel.chat.id == chat.iid).delete()
