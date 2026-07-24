from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aiogram.types import Message

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

_GETCHAT_COOLDOWN_SECONDS = 3600


@dataclass(frozen=True)
class CommunityRef:
    id: int
    name: str | None


class CommunityChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True)
class CommunityChange:
    kind: CommunityChangeKind
    community: CommunityRef | None


def extract_community_change(message: Message) -> CommunityChange | None:
    """Detect a community add/remove service message from native aiogram types."""
    if message.community_chat_added is not None:
        community = message.community_chat_added.community
        return CommunityChange(
            CommunityChangeKind.ADDED,
            CommunityRef(id=community.id, name=community.name),
        )
    if message.community_chat_removed is not None:
        return CommunityChange(CommunityChangeKind.REMOVED, None)
    return None


async def fetch_chat_community(chat_tid: int) -> CommunityRef | None:
    """Read the community of a chat via getChat, for chats that joined before Sophie.

    Guarded by a Redis cooldown so a chat whose community is unknown is only probed
    once per hour instead of on every message. Returns ``None`` while cooling down.
    """
    cooldown_key = f"sophie:community_getchat:{chat_tid}"
    if await aredis.exists(cooldown_key):
        return None
    await aredis.set(cooldown_key, b"1", ex=_GETCHAT_COOLDOWN_SECONDS)

    chat_full = await common_try(bot.get_chat(chat_tid))
    if chat_full is None:
        return None

    community = chat_full.community
    if community is None:
        return None
    ref = CommunityRef(id=community.id, name=community.name)
    log.debug("community_api: fetch_chat_community", chat_tid=chat_tid, found=ref is not None)
    return ref
