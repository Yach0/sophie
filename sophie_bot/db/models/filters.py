from typing import Any, Optional, Self, TypeVar

from aiogram.fsm.context import FSMContext
from beanie import Document
from beanie.odm.operators.update.general import Set
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from ._link_type import Link
from .chat import ChatModel

ACTION_DATA_DUMPED = dict[str, Any] | None
ACTION_DATA = TypeVar("ACTION_DATA", bound=type[BaseModel] | None)


class FilterActionType(BaseModel):
    name: str
    data: Any


class FilterHandlerType(BaseModel):
    # Right now only keyword as string
    keyword: str


class FiltersModel(Document):
    chat: Link[ChatModel]

    handler: str  # old keyword handler

    action: Optional[str]  # None for modern filters
    actions: dict[str, ACTION_DATA_DUMPED] = Field(default_factory=dict)

    time: Optional[Any] = None

    model_config = ConfigDict(
        extra="allow",  # legacy workaround
    )

    class Settings:
        name = "filters"

    @staticmethod
    async def get_filters(chat_iid: ObjectId) -> list["FiltersModel"] | None:
        return await FiltersModel.find(FiltersModel.chat.id == chat_iid).to_list()

    @staticmethod
    async def get_by_keyword(chat_iid: ObjectId, keyword: str) -> Optional["FiltersModel"]:
        return await FiltersModel.find_one(FiltersModel.chat.id == chat_iid, FiltersModel.handler == keyword)

    @staticmethod
    async def get_legacy_by_keyword(chat_iid: ObjectId, keyword: str) -> list["FiltersModel"]:
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

    async def update_fields(self, filters_setup: "FilterInSetupType"):
        return await self.update(
            Set(
                {
                    "handler": filters_setup.handler.keyword,
                    "actions": filters_setup.actions,
                }
            )
        )


class FilterInSetupType(BaseModel):
    """Information about the filter, while being in the setup mode."""

    oid: Optional[str] = None  # Optional ObjectID of the FiltersModel object, if need to update, not save
    handler: FilterHandlerType
    actions: dict[str, ACTION_DATA_DUMPED]

    @staticmethod
    async def get_filter(state: FSMContext, data: Optional[dict[str, Any]] = None) -> "FilterInSetupType":
        if data and "filter_in_setup" in data:
            return FilterInSetupType.model_validate(data["filter_in_setup"])

        if filter_item := await state.get_value("filter_in_setup"):
            return FilterInSetupType.model_validate(filter_item)

        raise ValueError("No filter in setup")

    async def set_filter_state(self, state: FSMContext) -> Self:
        await state.update_data(filter_in_setup=self.model_dump(mode="json"))
        return self

    def to_model(self, chat: ChatModel | ObjectId) -> FiltersModel:
        return FiltersModel(
            chat=chat,
            handler=self.handler.keyword,
            action=None,
            actions=self.actions,
        )

    @staticmethod
    def from_model(model: FiltersModel) -> "FilterInSetupType":
        return FilterInSetupType(
            oid=str(model.id),
            handler=FilterHandlerType(keyword=model.handler),
            actions=model.actions,
        )
