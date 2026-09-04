from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional

from aiogram.enums import ContentType
from aiogram.types import RichMessage
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


CURRENT_SAVEABLE_VERSION = 3


class Saveable(BaseModel):
    text: Annotated[str | None, Indexed(index_type=TEXT)] = ""

    file: NoteFile | None = None
    # Album (media group) items. When set (len > 1) the note is sent via sendMediaGroup
    # and `file` stays None. Single-media notes keep using `file` with `files` empty.
    files: list[NoteFile] = Field(default_factory=list)
    buttons: list[list[Button]] = Field(default_factory=list)

    parse_mode: SaveableParseMode | None = SaveableParseMode.html
    preview: bool | None = False
    rich_message: RichMessage | None = None

    version: int | None = 1


def normalize_notenames(notenames: Iterable[str]) -> tuple[str, ...]:
    """Note names are case-insensitive; they are stored and queried lowercased."""
    return tuple(name.lower() for name in notenames)


class NoteModel(Saveable, Document):
    # Old ID
    chat_tid: Annotated[int, Indexed()] = Field(..., alias="chat_id")

    # New link
    chat: Annotated[Link[ChatModel], Indexed()]

    names: tuple[str, ...]
    note_group: str | None = None

    description: str | None = None
    ai_description: bool = False
    embedding: list[float] | None = None
    embedding_text: str | None = None
    embedding_model: str | None = None

    created_date: datetime | None = None
    created_user: Link[ChatModel] | None = None
    edited_date: datetime | None = None
    edited_user: Link[ChatModel] | None = None

    @field_validator("names", mode="after")
    @classmethod
    def _normalize_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_notenames(value)

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
        return await NoteModel.find_one(
            NoteModel.chat.id == chat_iid, In(NoteModel.names, normalize_notenames(notenames))
        )

    @staticmethod
    async def delete_all_notes(chat_iid: PydanticObjectId) -> DeleteResult | None:
        return await NoteModel.find(NoteModel.chat.id == chat_iid).delete()
