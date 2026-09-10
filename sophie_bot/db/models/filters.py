from __future__ import annotations

from typing import Any

from beanie import Document
from bson import ObjectId
from pydantic import ConfigDict, Field

from ._link_type import Link
from .chat import ChatModel

ACTION_DATA_DUMPED = dict[str, Any] | None


class FiltersModel(Document):
    chat: Link[ChatModel]

    handler: str  # old keyword handler
    version: int | None = None

    action: str | None  # None for modern filters
    actions: dict[str, ACTION_DATA_DUMPED] = Field(default_factory=dict)

    # Silent mode: delete the triggering message and the filter's replies shortly after sending
    silent: bool = False

    time: Any | None = None

    model_config = ConfigDict(
        extra="ignore",
    )

    class Settings:
        name = "filters"

    @property
    def effective_version(self) -> int:
        return self.version or 1

    @staticmethod
    async def get_filters(chat_iid: ObjectId) -> list[FiltersModel] | None:
        return await FiltersModel.find(FiltersModel.chat.id == chat_iid).to_list()

    @staticmethod
    async def get_by_keyword(chat_iid: ObjectId, keyword: str) -> FiltersModel | None:
        return await FiltersModel.find_one(FiltersModel.chat.id == chat_iid, FiltersModel.handler == keyword)

    @staticmethod
    async def get_all_by_keyword(chat_iid: ObjectId, keyword: str) -> list[FiltersModel]:
        return await FiltersModel.find(FiltersModel.chat.id == chat_iid, FiltersModel.handler == keyword).to_list()

    @staticmethod
    async def get_by_id(oid: ObjectId):
        return await FiltersModel.find_one(FiltersModel.id == oid)

    @staticmethod
    async def count_ai_filters(chat_iid: ObjectId) -> int:
        """Count the number of AI filter handlers for a specific chat.

        AI filters are identified by handlers that start with 'ai:' prefix.

        Args:
            chat_iid: The database internal ID to count AI filters for.

        Returns:
            Number of AI filter handlers in the chat.
        """
        all_filters = await FiltersModel.get_filters(chat_iid)
        if not all_filters:
            return 0
        return sum(1 for filter_item in all_filters if filter_item.handler.startswith("ai:"))
