from typing import Optional

from beanie import PydanticObjectId

from sophie_bot.db.models import DisablingModel
from sophie_bot.modules.help.utils.extract_info import DISABLEABLE_CMDS, HandlerHelp


async def get_disabled_handlers(chat_iid: PydanticObjectId) -> tuple[HandlerHelp, ...]:
    disabled_cmds: list[str] = await DisablingModel.get_disabled(chat_iid)

    return tuple(handler for name, handler in DISABLEABLE_CMDS.items() if name in disabled_cmds)


def resolve_disableable_cmd(name: str) -> Optional[tuple[str, HandlerHelp]]:
    """Resolves a user-supplied command name to its canonical disable-able name and its help entry.

    Any of the handler's commands resolve to the same canonical name, so aliases cannot produce
    a second, unenforceable key.
    """
    return next(
        ((key, handler) for key, handler in DISABLEABLE_CMDS.items() if name == key or name in handler.cmds),
        None,
    )
