from __future__ import annotations

from pydantic import BaseModel, Field


class AIChatSummaryGroup(BaseModel):
    emoji: str
    title: str
    message_ids: list[int] = Field(default_factory=list, min_length=1)


class AIChatSummaryGroups(BaseModel):
    overview: str
    lines: list[AIChatSummaryGroup] = Field(default_factory=list)
