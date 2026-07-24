from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, ClassVar

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class AIChatSummaryLine(BaseModel):
    emoji: str
    title: str
    first_message_id: int
    first_message_at: datetime
    usernames: list[str] = Field(default_factory=list)
    source_excerpt: str | None = None


class AIChatSummaryModel(Document):
    chat: Annotated[Link[ChatModel], Indexed()]
    summary_date: date
    overview: str
    lines: list[AIChatSummaryLine] = Field(default_factory=list)

    class Settings:
        name = "ai_chat_summaries"
        indexes: ClassVar = [
            IndexModel(
                [
                    ("chat.$id", ASCENDING),
                    ("summary_date", DESCENDING),
                ],
                unique=True,
                name="chat_summary_date",
            ),
        ]

    @staticmethod
    async def get_for_date(chat_iid: PydanticObjectId, summary_date: date) -> AIChatSummaryModel | None:
        return await AIChatSummaryModel.find_one(
            AIChatSummaryModel.chat.id == chat_iid,
            AIChatSummaryModel.summary_date == summary_date,
        )

    @staticmethod
    async def upsert_for_date(
        chat: ChatModel, summary_date: date, overview: str, lines: list[AIChatSummaryLine]
    ) -> AIChatSummaryModel:
        model = await AIChatSummaryModel.find_one(
            AIChatSummaryModel.chat.id == chat.iid,
            AIChatSummaryModel.summary_date == summary_date,
        )
        if model:
            model.overview = overview
            model.lines = lines
            await model.save()
            return model

        model = AIChatSummaryModel(chat=chat, summary_date=summary_date, overview=overview, lines=lines)
        await model.save()
        return model

    @staticmethod
    async def get_recent_lines(chat_iid: PydanticObjectId, limit: int = 5) -> list[AIChatSummaryLine]:
        summaries = await (
            AIChatSummaryModel.find(AIChatSummaryModel.chat.id == chat_iid)
            .sort([("summary_date", DESCENDING)])
            .limit(limit)
            .to_list()
        )
        return [line for summary in reversed(summaries) for line in summary.lines]
