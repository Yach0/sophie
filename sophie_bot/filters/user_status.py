from aiogram.filters import Filter
from aiogram.types import Message

from sophie_bot.config import CONFIG


class IsOwner(Filter):
    key = "is_owner"

    def __init__(self, is_owner: bool):
        self.is_owner = is_owner

    async def __call__(self, message: Message) -> bool:
        if message.from_user and message.from_user.id == CONFIG.owner_id:
            return self.is_owner
        return not self.is_owner


class IsOP(Filter):
    key = "is_op"

    def __init__(self, is_op: bool):
        self.is_op = is_op

    async def __call__(self, message: Message) -> bool:
        if message.from_user and message.from_user.id in CONFIG.operators:
            return self.is_op
        return not self.is_op
