from __future__ import annotations

import asyncio

import httpx2
from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from sophie_bot.config import CONFIG
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext

_TINYFISH_SEARCH_URL = "https://api.search.tinyfish.ai"


class TinyFishSearchResult(BaseModel):
    title: str = Field(description="Search result title.")
    url: str = Field(description="Canonical URL for the result.")
    snippet: str | None = Field(default=None, description="Short search result snippet.")
    published: str | None = Field(default=None, description="Publication date if TinyFish returned one.")


def _search_tinyfish(query: str, limit: int) -> list[TinyFishSearchResult]:
    response = httpx2.get(
        _TINYFISH_SEARCH_URL,
        params={"query": query},
        headers={"X-API-Key": CONFIG.tinyfish_api_key},
    )
    response.raise_for_status()
    # The Search API exposes no count parameter, so trim client-side.
    return [
        TinyFishSearchResult(
            title=result["title"],
            url=result["url"],
            snippet=result.get("snippet"),
            published=result.get("date"),
        )
        for result in response.json().get("results", [])[:limit]
    ]


async def search_tinyfish(query: str, limit: int = 5) -> list[TinyFishSearchResult]:
    limited_results = max(1, min(limit, 10))
    return await asyncio.to_thread(_search_tinyfish, query, limited_results)


async def tinyfish_search(
    ctx: RunContext[SophieAIToolContext], query: str, limit: int = 5
) -> list[TinyFishSearchResult]:
    """Search the web with TinyFish and return result metadata.

    Args:
        query: Search query to send to TinyFish.
        limit: Maximum number of results to return, from 1 to 10.
    """
    async with track_ai_tool("tinyfish_search"):
        return await search_tinyfish(query, limit)


tinyfish_search_tool = Tool(
    tinyfish_search,
    name="tinyfish_search",
    description="Search the web with TinyFish and return result titles, URLs, and snippets.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
