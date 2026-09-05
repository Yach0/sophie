from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from stfu_tg import Template, UserLink

from sophie_bot.modules.communities.services import CommunityBanService, CommunityManageService
from sophie_bot.modules.restrictions.utils.restrictions import ban_user
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class CommunityBanMiddleware(BaseMiddleware):
    """Enforce community bans on users who post in a community chat after being banned."""

    async def is_cbanned(self, message: Message, data: dict[str, Any]) -> bool:
        if message.sender_chat:
            return False
        if message.chat.type not in {"group", "supergroup"}:
            return False
        if not message.from_user:
            return False

        chat_db = data.get("chat_db")
        user_db = data.get("user_db")
        if not chat_db or not user_db:
            return False
        if chat_db.community_tid is None:
            return False

        if not await is_enabled("communities", chat_tid=chat_db.tid):
            return False

        user_id = user_db.tid
        user_name = message.from_user.first_name
        chat_id = chat_db.tid

        community = await CommunityManageService.get_community_for_chat(chat_db)
        if not community:
            return False

        # Skip automatic enforcement for admins and globally whitelisted users.
        if await is_user_globally_whitelisted(user_id) or await is_user_admin(chat_db.iid, user_db.iid):
            return False

        ban = await CommunityBanService.is_user_banned(community.community_tid, user_id)
        if not ban:
            return False

        log.debug(f"Enforcing cban on {user_id} in {chat_id}")

        if not await ban_user(chat_id, user_id):
            return True

        doc = Template(
            _("{user} is banned in this community."),
            user=UserLink(user_id, user_name),
        )
        if ban.reason:
            doc += Template(_("Reason: {text}"), text=ban.reason)

        await common_try(message.reply(str(doc)))

        # Record the chat where the ban was now applied.
        if chat_db.iid not in [banned_chat.to_ref().id for banned_chat in ban.banned_chats]:
            ban.banned_chats.append(chat_db)
            await ban.save()

        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and await self.is_cbanned(event, data):
            return None

        return await handler(event, data)
