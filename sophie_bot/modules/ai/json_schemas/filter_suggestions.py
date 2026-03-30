from __future__ import annotations

from pydantic import BaseModel, Field


class AIFilterSuggestion(BaseModel):
    handler: str = Field(description="Filter handler ready to pass to /addfilter")
    description: str = Field(description="Short plain-language description of what the handler does")
    note: str = Field(description="One-line practical caveat or endorsement without emoji")
    recommended: bool = Field(description="Whether this is the best overall suggestion")


class AIFilterSuggestionsResponse(BaseModel):
    suggestions: list[AIFilterSuggestion] = Field(
        min_length=1,
        max_length=3,
        description="One to three unique filter suggestions ordered from best to worst",
    )
