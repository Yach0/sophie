from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB, WelcomeSecurityMoveCB
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.handlers.captcha_get import CaptchaGetHandler
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha

GROUP_A_TID = -1006001
GROUP_B_TID = -1006002


async def _insert_group(tid: int, title: str) -> ChatModel:
    return await ChatModel(
        tid=tid,
        type=ChatType.supergroup,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()


class FakeState:
    """Minimal FSMContext stand-in that records what the handler persists."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)
        self.cleared = False
        self.current_state: Any = None

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def set_state(self, state: Any) -> None:
        self.current_state = state

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
            "ws_chat_iid": str(group_a.id),
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
        ws_chat_iid=group_b.id,
        ws_is_join_request=False,
    )
    await handler.handle()

    caption = answer_media.await_args.kwargs["caption"]
    assert group_b.first_name_or_title in caption
    assert group_a.first_name_or_title not in caption

    assert state._data["ws_chat_iid"] == str(group_b.id)
    assert state._data["ws_is_join_request"] is False

    markup = answer_media.await_args.kwargs["reply_markup"]
    confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
    assert confirm.chat_iid == str(group_b.id)
    assert confirm.is_join_request is False


@pytest.mark.asyncio
async def test_legacy_button_does_not_reuse_other_chats_captcha(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The abandoned chat's captcha progress must not be carried into the requested chat."""
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)
    stale_captcha = state._data["captcha"]

    monkeypatch.setattr(CaptchaGetHandler, "answer_media", AsyncMock())

    handler = CaptchaGetHandler(SimpleNamespace(), state=state, ws_chat_iid=group_b.id)
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
        callback_data=WelcomeSecurityMoveCB(direction="right", chat_iid=str(group_b.id), is_join_request=False),
    )
    await handler.handle()

    caption = answer_media.await_args.kwargs["caption"]
    assert group_b.first_name_or_title in caption
    assert state._data["ws_chat_iid"] == str(group_b.id)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("is_join_request", [False, True])
async def test_same_chat_callback_keeps_its_join_request_mode(
    db_init: Any, monkeypatch: pytest.MonkeyPatch, is_join_request: bool
) -> None:
    del db_init
    group, _other_group = await _build_groups()
    state = _abandoned_state(group)
    state._data["ws_is_join_request"] = not is_join_request
    previous_data = await state.get_data()
    expected_captcha = EmojiCaptcha(data=previous_data["captcha"])
    expected_captcha.data.move_to_left()

    async def answer_media(*_args: Any, **kwargs: Any) -> None:
        # A failed Telegram edit must leave the previous progress available for retry.
        assert await state.get_data() == previous_data
        assert state.current_state is None
        markup = kwargs["reply_markup"]
        for button in markup.inline_keyboard[0]:
            movement = WelcomeSecurityMoveCB.unpack(button.callback_data)
            assert movement.chat_iid == str(group.id)
            assert movement.is_join_request is is_join_request
        confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
        assert confirm.chat_iid == str(group.id)
        assert confirm.is_join_request is is_join_request

    monkeypatch.setattr(CaptchaGetHandler, "answer_media", AsyncMock(side_effect=answer_media))
    handler = CaptchaGetHandler(
        SimpleNamespace(),
        state=state,
        callback_data=WelcomeSecurityMoveCB(
            direction="left", chat_iid=str(group.id), is_join_request=is_join_request
        ),
    )
    await handler.handle()

    assert state._data["captcha"] == expected_captcha.data.model_dump()
    assert state._data["ws_chat_iid"] == str(group.id)
    assert state._data["ws_is_join_request"] is is_join_request
    assert state.current_state == WelcomeSecurityFSM.captcha


@pytest.mark.asyncio
async def test_explicit_target_does_not_inherit_callback_join_request_mode(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)
    answer_media = AsyncMock()
    monkeypatch.setattr(CaptchaGetHandler, "answer_media", answer_media)

    handler = CaptchaGetHandler(
        SimpleNamespace(),
        state=state,
        ws_chat_iid=group_b.id,
        callback_data=WelcomeSecurityMoveCB(
            direction="right", chat_iid=str(group_a.id), is_join_request=True
        ),
    )
    await handler.handle()

    assert state._data["ws_chat_iid"] == str(group_b.id)
    assert state._data["ws_is_join_request"] is False
    markup = answer_media.await_args.kwargs["reply_markup"]
    confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
    assert confirm.chat_iid == str(group_b.id)
    assert confirm.is_join_request is False


@pytest.mark.asyncio
async def test_wrong_answer_redraw_keeps_target_and_displays_retry_message(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_init
    group, _other_group = await _build_groups()
    state = _abandoned_state(group)
    answer_media = AsyncMock()
    monkeypatch.setattr(CaptchaGetHandler, "answer_media", answer_media)

    await CaptchaGetHandler(SimpleNamespace(), state=state, ws_shuffle=True).handle()

    assert "❌ Incorrect solution. Please, try again." in answer_media.await_args.kwargs["caption"]
    assert group.first_name_or_title in answer_media.await_args.kwargs["caption"]
    assert state._data["ws_chat_iid"] == str(group.id)
    assert state._data["ws_is_join_request"] is True
    markup = answer_media.await_args.kwargs["reply_markup"]
    confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
    assert confirm.chat_iid == str(group.id)
    assert confirm.is_join_request is True


@pytest.mark.asyncio
async def test_failed_redraw_keeps_previous_captcha_state(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_init
    group_a, group_b = await _build_groups()
    state = _abandoned_state(group_a)
    previous_data = await state.get_data()
    monkeypatch.setattr(
        CaptchaGetHandler, "answer_media", AsyncMock(side_effect=RuntimeError("Telegram edit failed"))
    )

    handler = CaptchaGetHandler(SimpleNamespace(), state=state, ws_chat_iid=group_b.id)
    with pytest.raises(RuntimeError, match="Telegram edit failed"):
        await handler.handle()

    assert await state.get_data() == previous_data
    assert state.current_state is None
