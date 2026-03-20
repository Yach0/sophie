from __future__ import annotations

from typing import Optional

from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgStrictError
from ass_tg.types import WordArg
from babel.support import LazyProxy

from sophie_bot.modules.locks.utils.lock_types import ALL_LOCK_TYPES, is_language_lock, is_stickerpack_lock
from sophie_bot.utils.i18n import lazy_gettext as l_


class LockTypeArg(WordArg):
    """Argument type for lock types, supporting standard locks, stickerpack:PACK_ID and language:LANG_CODE formats."""

    def __init__(self, description: Optional[LazyProxy | str] = None):
        super().__init__(description=description or l_("Lock type"))

    def check(self, text: str, entities: ArgEntities) -> bool:
        word = text.split()[0].lower() if text.split() else ""
        return word in ALL_LOCK_TYPES or is_stickerpack_lock(word) or is_language_lock(word)

    async def value(self, text: str) -> str:
        lock_type = text.lower()

        if lock_type in ALL_LOCK_TYPES:
            return lock_type

        if is_stickerpack_lock(lock_type):
            return lock_type

        if is_language_lock(lock_type):
            return lock_type

        raise ArgStrictError(
            l_(
                "Unknown lock type. Use /lockable to see all available lock types, or use stickerpack:PACK_ID or language:LANG format."
            )
        )

    def needed_type(self) -> tuple[LazyProxy, LazyProxy]:
        return l_("Lock type (e.g., sticker, url, stickerpack:PACK_ID, language:ru)"), l_("Lock types")

    @property
    def examples(self) -> dict[str, Optional[LazyProxy]] | None:
        return {
            "sticker": l_("Block stickers"),
            "url": l_("Block URLs"),
            "forward": l_("Block forwarded messages"),
            "stickerpack:MyStickers": l_("Block specific sticker pack"),
            "language:ru": l_("Block Russian language messages"),
        }
