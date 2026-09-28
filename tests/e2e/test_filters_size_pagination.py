from __future__ import annotations

from html.parser import HTMLParser

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.filters.callbacks import FiltersPageCallback
from tests.e2e.helpers import next_group_id, next_user_id


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data.encode("utf-16-le")) // 2


def _rich_request(requests):
    return next(request for request in requests if isinstance(request.params, dict) and "rich_message" in request.params)


def _page_details(requests):
    request = _rich_request(requests)
    html = request.params["rich_message"]["html"]
    counter = _VisibleText()
    counter.feed(html)
    assert counter.length <= 4096
    return html, [
        FiltersPageCallback.unpack(button["callback_data"]).page
        for row in (request.reply_markup or {}).get("inline_keyboard", [])
        for button in row
    ]


@pytest.mark.asyncio
async def test_filters_split_long_entries_and_keep_controls_on_every_page(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=next_group_id(), title="Long Filters")
    user = test_client.create_user(user_id=next_user_id(), first_name="Reader")
    await test_client.send_message(text="init", from_user=user.user, chat=group)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    items = []
    for index in range(11):
        items.append(
            await FiltersModel(
                chat=chat.iid,
                handler=f"filter-{index:02d}-<script>" + (str(index) * 1200),
                action=None,
                actions={"reply": {"text": "response"}},
            ).insert()
        )

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", is_bot=True)
    message = MessageFactory.create(text="Filters", from_user=bot_user, chat=group)
    requests = await test_client.send_command(command="filters", from_user=user.user, chat=group)
    seen: set[str] = set()
    page_number = 0
    while True:
        html, navigation = _page_details(requests)
        assert "<script>" not in html
        for item in items:
            if str(item.id) in html:
                assert str(item.id) in html[html.index("<tg-button-row") :]
                seen.add(str(item.id))
        assert navigation[:1] == ([page_number - 1] if page_number else [page_number + 1])
        if page_number > 0 and page_number + 1 in navigation:
            assert navigation == [page_number - 1, page_number + 1]
        assert page_number < len(items)
        if page_number + 1 not in navigation:
            break
        page_number += 1
        requests = await test_client.send_callback(
            FiltersPageCallback(page=page_number).pack(), from_user=user.user, message=message
        )
    assert page_number >= 3  # Eight long entries cannot fit together on the first page.
    assert seen == {str(item.id) for item in items}
    previous = await test_client.send_callback(
        FiltersPageCallback(page=page_number - 1).pack(), from_user=user.user, message=message
    )
    _, previous_navigation = _page_details(previous)
    assert page_number in previous_navigation
    clamped = await test_client.send_callback(
        FiltersPageCallback(page=999).pack(), from_user=user.user, message=message
    )
    _, last_navigation = _page_details(clamped)
    assert last_navigation == [page_number - 1]


@pytest.mark.asyncio
async def test_filters_single_oversized_handler_is_excerpted_without_losing_controls(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=next_group_id(), title="Oversized Filter")
    user = test_client.create_user(user_id=next_user_id(), first_name="Reader")
    await test_client.send_message(text="init", from_user=user.user, chat=group)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    oversized = await FiltersModel(
        chat=chat.iid,
        handler="<unsafe>" + "🧪" * 5000,
        action=None,
        actions={"reply": {"text": "response"}},
    ).insert()
    normal = await FiltersModel(
        chat=chat.iid, handler="next-filter", action=None, actions={"reply": {"text": "response"}}
    ).insert()

    requests = await test_client.send_command(command="filters", from_user=user.user, chat=group)
    html, navigation = _page_details(requests)
    assert "&lt;unsafe&gt;" in html
    assert "<unsafe>" not in html
    assert "…" in html
    assert str(oversized.id) in html
    assert str(normal.id) in html
    assert html.count("<tg-button-row") == 2
    assert navigation == []
