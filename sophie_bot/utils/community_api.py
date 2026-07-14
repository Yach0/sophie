from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from aiogram.types import Message

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

# Bot API 10.2 introduced Communities, but the installed aiogram (3.29.x, API 10.1) does not
# model them yet. aiogram keeps unknown fields in ``.model_extra``, so we bridge the raw
# ``community_chat_added`` / ``community_chat_removed`` service messages and the
# ``ChatFullInfo.community`` field by hand here. THIS MODULE is the single place to swap over
# to native aiogram types once a release targeting API >= 10.2 ships.

_GETCHAT_COOLDOWN_SECONDS = 3600


@dataclass(frozen=True)
class CommunityRef:
    id: int
    name: Optional[str]


class CommunityChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True)
class CommunityChange:
    kind: CommunityChangeKind
    # None for removals (and additions) where Telegram does not echo the community object back.
    community: Optional[CommunityRef]


def _parse_community_dict(raw: Any) -> Optional[CommunityRef]:
    """Parse a raw community payload into a CommunityRef, tolerating shape drift.

    The payload may be the Community object directly, or a service-message wrapper
    carrying it under a ``community`` key. Exact 10.2 field names are not available to
    type-check against, so parse defensively and bail out when there's no usable id.
    """
    if not isinstance(raw, dict):
        return None
    nested = raw.get("community")
    payload = nested if isinstance(nested, dict) else raw
    community_id = payload.get("id")
    if not isinstance(community_id, int):
        return None
    name = payload.get("name") or payload.get("title")
    return CommunityRef(id=community_id, name=name if isinstance(name, str) else None)


def extract_community_change(message: Message) -> Optional[CommunityChange]:
    """Detect a community add/remove service message from raw update fields."""
    extra = message.model_extra or {}
    if "community_chat_added" in extra:
        return CommunityChange(CommunityChangeKind.ADDED, _parse_community_dict(extra.get("community_chat_added")))
    if "community_chat_removed" in extra:
        return CommunityChange(CommunityChangeKind.REMOVED, _parse_community_dict(extra.get("community_chat_removed")))
    return None


async def fetch_chat_community(chat_tid: int) -> Optional[CommunityRef]:
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

    community = (chat_full.model_extra or {}).get("community")
    ref = _parse_community_dict(community)
    log.debug("community_api: fetch_chat_community", chat_tid=chat_tid, found=ref is not None)
    return ref
