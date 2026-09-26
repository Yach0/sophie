from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AIFilterResponseSchema(BaseModel):
    """Schema for AI filter evaluation response."""

    matches: bool = Field(
        description="Whether the message content matches the filter criteria described in the user prompt"
    )
    reasoning: str = Field(description="Brief explanation of why the content does or does not match the criteria")


class JevNoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class JevResponse(BaseModel):
    model: str
    answers: dict[str, JevNoulAnswer]
