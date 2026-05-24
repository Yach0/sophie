from __future__ import annotations

import asyncio

import openapi_client
from openapi_client.models.search_request import SearchRequest
from openapi_client.models.search_result import SearchResult
from pydantic_ai import RunContext, Tool
from typing_extensions import TypedDict

from sophie_bot.config import CONFIG
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt


class KagiSearchResult(TypedDict):
    title: str
    url: str
    snippet: str | None
    published: str | None


def _from_search_result(result: SearchResult) -> KagiSearchResult:
    return {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "published": result.time,
    }


def _search_kagi(query: str, limit: int) -> list[KagiSearchResult]:
    configuration = openapi_client.Configuration(access_token=CONFIG.kagi_api_key)
    with openapi_client.ApiClient(configuration) as api_client:
        search_api = openapi_client.SearchApi(api_client)
        response = search_api.search(SearchRequest(query=query, limit=limit, workflow="search"))

    if response.data is None or response.data.search is None:
        return []

    return [_from_search_result(result) for result in response.data.search]


class KagiSearchAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt], query: str, limit: int = 5) -> list[KagiSearchResult]:
        async with track_ai_tool("kagi_search"):
            _ = ctx
            limited_results = max(1, min(limit, 10))
            return await asyncio.to_thread(_search_kagi, query, limited_results)

    def __new__(cls) -> Tool[SophieAIToolContenxt]:
        return Tool(
            cls.tool_call,
            name="kagi_search",
            description="Search the web with Kagi and return result titles, URLs, snippets, and publication dates.",
            takes_ctx=True,
        )


def kagi_search_ai_tool() -> Tool[SophieAIToolContenxt]:
    return KagiSearchAgentTool()
