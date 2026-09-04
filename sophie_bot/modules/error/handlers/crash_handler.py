from aiogram.types import Message

from sophie_bot.utils.i18n import gettext as _


async def crash_handler(message: Message):
    await message.reply(_("Crashing..."))

    raise ZeroDivisionError
