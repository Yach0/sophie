from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchSearchQuery(BaseModel):
    query: str = Field(description="Search query to execute.")
    reason: str = Field(description="Why this query is useful for the research task.")


class ResearchQueryPlan(BaseModel):
    queries: list[ResearchSearchQuery] = Field(description="Search queries for this research stage.")


class ResearchSource(BaseModel):
    title: str = Field(description="Source title.")
    url: str = Field(description="Canonical source URL.")
    snippet: str | None = Field(default=None, description="Short result snippet or evidence summary.")
    published: str | None = Field(default=None, description="Publication date or time if known.")


class ResearchDecision(BaseModel):
    action: Literal["search", "continue"] = Field(description="Whether to run follow-up searches or continue.")
    followup_queries: list[ResearchSearchQuery] = Field(
        default_factory=list,
        description="Follow-up searches to run when action is search.",
    )
    reasoning: str = Field(description="Brief explanation of the decision.")


class ResearchFinalResponse(BaseModel):
    research_title: str = Field(description="Short filesystem-safe title for the research result.")
    text: str = Field(description="Final research summary.")
    sources: list[ResearchSource] = Field(description="Sources used to support the summary.")
    research_query: str | None = Field(default=None, description="Original research request.")
    research_model: str | None = Field(default=None, description="Research model used to produce the summary.")
