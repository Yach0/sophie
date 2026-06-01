from __future__ import annotations

import asyncio

import openapi_client
from openapi_client.models.search_request import SearchRequest
from openapi_client.models.search_result import SearchResult
from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from sophie_bot.config import CONFIG
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext


class KagiSearchResult(BaseModel):
    title: str = Field(description="Search result title.")
    url: str = Field(description="Canonical URL for the result.")
    snippet: str | None = Field(default=None, description="Short search result snippet.")
    published: str | None = Field(default=None, description="Publication date or time if Kagi returned one.")


def _from_search_result(result: SearchResult) -> KagiSearchResult:
    return KagiSearchResult(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        published=result.time,
    )


def _search_kagi(query: str, limit: int) -> list[KagiSearchResult]:
    configuration = openapi_client.Configuration(access_token=CONFIG.kagi_api_key)
    with openapi_client.ApiClient(configuration) as api_client:
        search_api = openapi_client.SearchApi(api_client)
        response = search_api.search(SearchRequest(query=query, limit=limit, workflow="search"))

    if response.data is None or response.data.search is None:
        return []

    return [_from_search_result(result) for result in response.data.search]


async def search_kagi(query: str, limit: int = 5) -> list[KagiSearchResult]:
    limited_results = max(1, min(limit, 10))
    return await asyncio.to_thread(_search_kagi, query, limited_results)


async def kagi_search(ctx: RunContext[SophieAIToolContext], query: str, limit: int = 5) -> list[KagiSearchResult]:
    """Search the web with Kagi and return result metadata.

    Args:
        query: Search query to send to Kagi.
        limit: Maximum number of results to return, from 1 to 10.
    """
    async with track_ai_tool("kagi_search"):
        return await search_kagi(query, limit)


kagi_search_tool = Tool(
    kagi_search,
    name="kagi_search",
    description="Search the web with Kagi and return result titles, URLs, snippets, and publication dates.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
