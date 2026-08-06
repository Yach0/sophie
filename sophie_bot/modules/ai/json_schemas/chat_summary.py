from __future__ import annotations

from pydantic import BaseModel, Field


class AIChatSummaryGroup(BaseModel):
    emoji: str
    title: str
    message_refs: list[int] = Field(
        default_factory=list,
        min_length=1,
        description="References to the transcript lines this topic is built from, exactly as shown at the start of each line.",
    )


class AIChatSummaryGroups(BaseModel):
    overview: str
    lines: list[AIChatSummaryGroup] = Field(default_factory=list)
