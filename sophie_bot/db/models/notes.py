from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional, Sequence

from aiogram.enums import ContentType
from beanie import Document, Indexed, PydanticObjectId
from beanie.odm.operators.find.comparison import In
from beanie.odm.operators.find.evaluation import Text
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import TEXT
from pymongo.results import DeleteResult

from ._link_type import Link
from .chat import ChatModel
from .notes_buttons import Button


class NoteFile(BaseModel):
    id: str
    type: ContentType

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SaveableParseMode(Enum):
    markdown = "md"
    html = "html"


CURRENT_SAVEABLE_VERSION = 2


class Saveable(BaseModel):
    text: Annotated[Optional[str], Indexed(index_type=TEXT)] = ""

    file: Optional[NoteFile] = None
    # Album (media group) items. When set (len > 1) the note is sent via sendMediaGroup
    # and `file` stays None. Single-media notes keep using `file` with `files` empty.
    files: list[NoteFile] = Field(default_factory=list)
    buttons: list[list[Button]] = Field(default_factory=list)

    parse_mode: Optional[SaveableParseMode] = SaveableParseMode.html
    preview: Optional[bool] = False

    version: Optional[int] = 1


class NoteModel(Saveable, Document):
    # Old ID
    chat_tid: Annotated[int, Indexed()] = Field(..., alias="chat_id")

    # New link
    chat: Annotated[Link[ChatModel], Indexed()]

    names: tuple[str, ...]
    note_group: Optional[str] = None

    description: Optional[str] = None
    ai_description: bool = False
    embedding: Optional[list[float]] = None
    embedding_text: Optional[str] = None
    embedding_model: Optional[str] = None

    created_date: Optional[datetime] = None
    created_user: Optional[Link[ChatModel]] = None
    edited_date: Optional[datetime] = None
    edited_user: Optional[Link[ChatModel]] = None

    @field_validator("created_user", "edited_user", mode="before")
    @classmethod
    def _coerce_legacy_user_link(cls, value: Any) -> Any:
        # Pre-4.0 these held a raw Telegram user ID rather than a link. Attribution is not
        # recoverable here, so drop it instead of failing the whole read.
        if isinstance(value, int):
            return None
        return value

    class Settings:
        name = "notes"

    @staticmethod
    async def get_chat_notes(chat_iid: PydanticObjectId) -> list["NoteModel"]:
        return await NoteModel.find(NoteModel.chat.id == chat_iid).to_list()

    @staticmethod
    async def search_chat_notes(chat_iid: PydanticObjectId, text: str) -> list["NoteModel"]:
        return await NoteModel.find(NoteModel.chat.id == chat_iid, Text(text)).to_list()

    @staticmethod
    async def get_by_notenames(chat_iid: PydanticObjectId, notenames: Sequence[str]) -> Optional["NoteModel"]:
        return await NoteModel.find_one(NoteModel.chat.id == chat_iid, In(NoteModel.names, notenames))

    @staticmethod
    async def delete_all_notes(chat_iid: PydanticObjectId) -> DeleteResult | None:
        return await NoteModel.find(NoteModel.chat.id == chat_iid).delete()
