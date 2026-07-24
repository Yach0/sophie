from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB, WelcomeSecurityMoveCB
from sophie_bot.modules.welcomesecurity.handlers.captcha_get import CaptchaGetHandler
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha

GROUP_A_TID = -1006001
GROUP_B_TID = -1006002


async def _insert_group(tid: int, title: str) -> ChatModel:
    """Insert and re-fetch: Beanie assigns `_id` on insert, so the in-memory `iid` is stale until reloaded."""
    await ChatModel(
        tid=tid,
        type=ChatType.supergroup,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()

    group = await ChatModel.get_by_tid(tid)
    assert group is not None
    return group


class FakeState:
    """Minimal FSMContext stand-in that records what the handler persists."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)
        self.cleared = False

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def set_state(self, _state: Any) -> None:
        return None

    async def update_data(self, data: dict[str, Any]) -> None:
        self._data.update(data)

    async def clear(self) -> None:
        self.cleared = True
        self._data = {}


async def _build_groups() -> tuple[ChatModel, ChatModel]:
    await ChatModel.delete_all()
    group_a = await _insert_group(GROUP_A_TID, "Group A")
    group_b = await _insert_group(GROUP_B_TID, "Group B")
    return group_a, group_b


def _abandoned_state(group_a: ChatModel) -> FakeState:
    """State left behind by an abandoned captcha for group A."""
    return FakeState(
        {
            "ws_chat_iid": str(group_a.iid),
            "ws_is_join_request": True,
            "captcha": EmojiCaptcha().data.model_dump(),
        }
    )


@pytest.mark.asyncio
async def test_legacy_button_request_beats_stale_state(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A validated ws_chat_iid must win over a leftover captcha for another chat."""
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)

    answer_media = AsyncMock()
    monkeypatch.setattr(CaptchaGetHandler, "answer_media", answer_media)

    handler = CaptchaGetHandler(
        SimpleNamespace(),
        state=state,
        ws_chat_iid=group_b.iid,
        ws_is_join_request=False,
    )
    await handler.handle()

    caption = answer_media.await_args.kwargs["caption"]
    assert group_b.first_name_or_title in caption
    assert group_a.first_name_or_title not in caption

    assert state._data["ws_chat_iid"] == str(group_b.iid)
    assert state._data["ws_is_join_request"] is False

    markup = answer_media.await_args.kwargs["reply_markup"]
    confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
    assert confirm.chat_iid == str(group_b.iid)
    assert confirm.is_join_request is False


@pytest.mark.asyncio
async def test_legacy_button_does_not_reuse_other_chats_captcha(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The abandoned chat's captcha progress must not be carried into the requested chat."""
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)
    stale_captcha = state._data["captcha"]

    monkeypatch.setattr(CaptchaGetHandler, "answer_media", AsyncMock())

    handler = CaptchaGetHandler(SimpleNamespace(), state=state, ws_chat_iid=group_b.iid)
    await handler.handle()

    assert state._data["captcha"] != stale_captcha


@pytest.mark.asyncio
async def test_move_callback_request_beats_stale_state(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """An arrow tapped on group B's captcha must not be answered with group A's captcha."""
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)

    answer_media = AsyncMock()
    monkeypatch.setattr(CaptchaGetHandler, "answer_media", answer_media)

    handler = CaptchaGetHandler(
        SimpleNamespace(),
        state=state,
        callback_data=WelcomeSecurityMoveCB(direction="right", chat_iid=str(group_b.iid), is_join_request=False),
    )
    await handler.handle()

    caption = answer_media.await_args.kwargs["caption"]
    assert group_b.first_name_or_title in caption
    assert state._data["ws_chat_iid"] == str(group_b.iid)


@pytest.mark.asyncio
async def test_state_is_used_when_no_chat_is_requested(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit request (/captcha), the in-progress captcha is resumed unchanged."""
    del db_init
    group_a, _group_b = await _build_groups()
    state = _abandoned_state(group_a)
    in_progress_captcha = state._data["captcha"]

    answer_media = AsyncMock()
    monkeypatch.setattr(CaptchaGetHandler, "answer_media", answer_media)

    handler = CaptchaGetHandler(SimpleNamespace(), state=state)
    await handler.handle()

    caption = answer_media.await_args.kwargs["caption"]
    assert group_a.first_name_or_title in caption
    assert state._data["captcha"] == in_progress_captcha
    assert state._data["ws_is_join_request"] is True
