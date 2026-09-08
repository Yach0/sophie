from collections.abc import Mapping

from beanie import PydanticObjectId

from sophie_bot.db.models import DisablingModel
from sophie_bot.modules.help.utils.extract_info import HandlerHelp


async def get_disabled_handlers(
    chat_iid: PydanticObjectId,
    disableable_commands: Mapping[str, HandlerHelp],
) -> tuple[HandlerHelp, ...]:
    disabled_cmds: list[str] = await DisablingModel.get_disabled(chat_iid)

    return tuple(handler for name, handler in disableable_commands.items() if name in disabled_cmds)


def resolve_disableable_cmd(
    name: str,
    disableable_commands: Mapping[str, HandlerHelp],
) -> tuple[str, HandlerHelp] | None:
    """Resolves a user-supplied command name to its canonical disable-able name and its help entry.

    Any of the handler's commands resolve to the same canonical name, so aliases cannot produce
    a second, unenforceable key.
    """
    return next(
        ((key, handler) for key, handler in disableable_commands.items() if name == key or name in handler.cmds),
        None,
    )
