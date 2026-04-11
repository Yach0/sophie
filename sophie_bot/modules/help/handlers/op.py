from aiogram.handlers import MessageHandler
from stfu_tg import Section

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES, HandlerHelp, ModuleHelp
from sophie_bot.modules.help.utils.format_help import format_handlers

OP_COMMANDS_MESSAGE_LENGTH_LIMIT = TELEGRAM_MESSAGE_LENGTH_LIMIT - 100


def _format_module_commands(module: ModuleHelp, handlers: list[HandlerHelp] | None = None) -> str:
    return str(Section(format_handlers(handlers or module.handlers), title=f"{module.name} {module.icon}"))


def _format_module_commands_chunks(module: ModuleHelp) -> list[str]:
    module_text = _format_module_commands(module)
    if len(module_text) <= OP_COMMANDS_MESSAGE_LENGTH_LIMIT:
        return [module_text]

    return [_format_module_commands(module, [handler]) for handler in module.handlers]


def format_op_commands_messages(modules: list[ModuleHelp]) -> list[str]:
    messages: list[str] = []
    current_message_parts: list[str] = []
    current_message_length = 0

    for module in modules:
        for module_text in _format_module_commands_chunks(module):
            separator_length = 2 if current_message_parts else 0
            next_message_length = current_message_length + separator_length + len(module_text)

            if current_message_parts and next_message_length > OP_COMMANDS_MESSAGE_LENGTH_LIMIT:
                messages.append("\n\n".join(current_message_parts))
                current_message_parts = [module_text]
                current_message_length = len(module_text)
                continue

            current_message_parts.append(module_text)
            current_message_length = next_message_length

    if current_message_parts:
        messages.append("\n\n".join(current_message_parts))

    return messages


class OpCMDSList(MessageHandler):
    async def handle(self) -> None:
        for text in format_op_commands_messages(list(HELP_MODULES.values())):
            await self.event.reply(text)
