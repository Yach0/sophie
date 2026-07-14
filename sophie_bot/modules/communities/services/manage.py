from __future__ import annotations

from typing import Optional

from sophie_bot.db.models import ChatModel, CommunityModel
from sophie_bot.utils.community_api import fetch_chat_community


class CommunityManageService:
    """Resolution of the community a chat belongs to."""

    @staticmethod
    async def get_community_for_chat(chat_db: ChatModel) -> Optional[CommunityModel]:
        """Return the community for a chat, backfilling lazily via getChat when unknown.

        Chats that joined a community before Sophie never emit a service message, so when
        the stored edge is missing we probe once (cooldown-guarded) and persist the result.
        """
        if chat_db.community_tid is not None:
            return await CommunityModel.find_one(CommunityModel.community_tid == chat_db.community_tid)

        ref = await fetch_chat_community(chat_db.tid)
        if ref is None:
            return None

        community = await CommunityModel.ensure_community(ref.id, ref.name)
        await chat_db.set_community(ref.id)
        return community
