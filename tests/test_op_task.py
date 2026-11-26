import json
from functools import partial

import httpx2
import pytest

from sophie_bot.modules.op.handlers.op_task import _create_gitlab_issue


@pytest.mark.parametrize("redirect", [False, True])
async def test_create_gitlab_issue_keeps_token_on_gitlab(monkeypatch: pytest.MonkeyPatch, redirect: bool) -> None:
    received: list[httpx2.Request] = []
    issue = {"iid": 17, "web_url": "https://gitlab.com/sophie/bot/-/issues/17"}

    def respond(request: httpx2.Request) -> httpx2.Response:
        received.append(request)
        if redirect:
            return httpx2.Response(307, headers={"Location": "https://example.com/issues"})
        return httpx2.Response(201, json=issue)

    monkeypatch.setattr(httpx2, "AsyncClient", partial(httpx2.AsyncClient, transport=httpx2.MockTransport(respond)))

    if redirect:
        with pytest.raises(httpx2.HTTPStatusError) as error:
            await _create_gitlab_issue("test-token", "123", "Title", "Description", ["bug", "bot"])
        assert error.value.response.status_code == 307
    else:
        assert await _create_gitlab_issue("test-token", "123", "Title", "Description", ["bug", "bot"]) == issue

    (request,) = received
    assert request.method == "POST"
    assert str(request.url) == "https://gitlab.com/api/v4/projects/123/issues"
    assert request.headers["PRIVATE-TOKEN"] == "test-token"
    assert json.loads(request.content) == {"title": "Title", "description": "Description", "labels": "bug,bot"}
