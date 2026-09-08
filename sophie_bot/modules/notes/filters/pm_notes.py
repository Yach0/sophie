from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import TelegramObject

from sophie_bot.db.models import PrivateNotesModel
from sophie_bot.middlewares.request_context import RequestContext


class PMNotesFilter(Filter):
    async def __call__(
        self,
        event: TelegramObject,
        context: RequestContext,
    ) -> bool:
        del event
        connection = context.connection
        if not connection:
            raise ValueError("Missing connection in request context")

        if not connection.db_model:
            raise ValueError("Missing db_model in connection")

        private_notes_enabled: bool = await PrivateNotesModel.get_state(connection.db_model.iid)

        return private_notes_enabled
