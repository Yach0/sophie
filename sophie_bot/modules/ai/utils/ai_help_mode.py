from __future__ import annotations

from datetime import timedelta

from sophie_bot.services.redis import aredis

# Sophie-help is a temporary detour, not a setting: it lasts one conversation and expires on its
# own, so a user who wandered in from /help days ago is back to the normal PM assistant.
HELP_MODE_TTL = timedelta(hours=2)


def _key(chat_tid: int) -> str:
    return f"ai_help_mode:{chat_tid}"


async def activate_help_mode(chat_tid: int) -> None:
    await aredis.set(_key(chat_tid), b"1", ex=HELP_MODE_TTL)


async def deactivate_help_mode(chat_tid: int) -> None:
    await aredis.delete(_key(chat_tid))


async def is_help_mode(chat_tid: int) -> bool:
    return bool(await aredis.exists(_key(chat_tid)))
