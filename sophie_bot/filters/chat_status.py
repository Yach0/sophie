from aiogram.enums import ChatType
from aiogram.filters import Filter
from aiogram.types import Chat, TelegramObject


class ChatTypeFilter(Filter):
    def __init__(self, *chat_types: str | ChatType):
        self.chat_types = chat_types

    async def __call__(self, event: TelegramObject, event_chat: Chat, **kwargs) -> bool:
        return event_chat.type in self.chat_types
