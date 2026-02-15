from __future__ import annotations

from typing import Any, Dict, Union

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Filter
from aiogram.types import Message

from sophie_bot.constants import FEDERATION_BANLIST_COOLDOWN_SECONDS
from sophie_bot.services.redis import aredis
from sophie_bot.utils.i18n import gettext as _
from stfu_tg import Template


class BanlistCooldownFilter(Filter):
    async def __call__(self, message: Message) -> Union[bool, Dict[str, Any]]:
        if not message.from_user:
            raise SkipHandler

        key = f"fbanlist:cooldown:{message.from_user.id}"

        last_used = await aredis.get(key)
        if last_used:
            ttl = await aredis.ttl(key)
            await message.reply(
                Template(
                    _("⏱ Please wait {seconds} seconds before using this command again."), seconds=max(ttl, 1)
                ).to_html()
            )
            raise SkipHandler

        await aredis.setex(key, FEDERATION_BANLIST_COOLDOWN_SECONDS, "1")
        return True
