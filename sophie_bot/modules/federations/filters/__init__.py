from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Filter
from aiogram.types import Message
from stfu_tg import Template

from sophie_bot.constants import FEDERATION_BANLIST_COOLDOWN_SECONDS
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import gettext as _


class BanlistCooldownFilter(Filter):
    async def __call__(self, message: Message, services: ApplicationServices) -> bool | dict[str, Any]:
        if not message.from_user:
            raise SkipHandler

        key = f"fbanlist:cooldown:{message.from_user.id}"

        last_used = await services.redis.get(key)
        if last_used:
            ttl = await services.redis.ttl(key)
            await message.reply(
                Template(
                    _("⏱ Please wait {seconds} seconds before using this command again."), seconds=max(ttl, 1)
                ).to_html()
            )
            raise SkipHandler

        await services.redis.setex(key, FEDERATION_BANLIST_COOLDOWN_SECONDS, "1")
        return True
