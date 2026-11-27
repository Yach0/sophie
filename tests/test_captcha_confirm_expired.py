from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB, WelcomeSecurityRulesAgreeCB
from sophie_bot.modules.welcomesecurity.handlers.captcha_confirm import CaptchaConfirmHandler


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_type", [WelcomeSecurityConfirmCB, WelcomeSecurityRulesAgreeCB])
@pytest.mark.parametrize("state_status", ["empty", "missing_captcha", "missing_chat"])
async def test_stale_captcha_callback_answers_expired(
    monkeypatch: pytest.MonkeyPatch,
    callback_type: type[WelcomeSecurityConfirmCB] | type[WelcomeSecurityRulesAgreeCB],
    state_status: str,
) -> None:
    chat_iid = PydanticObjectId()
    state_data: dict[str, object] = {}
    if state_status != "empty":
        state_data["ws_chat_iid"] = str(chat_iid)
    if state_status == "missing_chat":
        # A stale chat reference must be rejected before parsing or completing the captcha.
        state_data["captcha"] = {}

    state = SimpleNamespace(get_data=AsyncMock(return_value=state_data), clear=AsyncMock())
    event = SimpleNamespace(answer=AsyncMock())
    get_chat = AsyncMock(return_value=None)
    monkeypatch.setattr(ChatModel, "get_by_iid", get_chat)
    captcha_correct = AsyncMock()
    monkeypatch.setattr(CaptchaConfirmHandler, "captcha_correct", captcha_correct)
    handler = CaptchaConfirmHandler(
        event,
        state=state,
        callback_data=callback_type(chat_iid=str(chat_iid)),
    )

    assert await handler.handle() is None

    event.answer.assert_awaited_once_with("Captcha expired. Please try again.")
    captcha_correct.assert_not_awaited()
    state.clear.assert_not_awaited()
    if state_status == "missing_chat":
        get_chat.assert_awaited_once_with(chat_iid)
    else:
        get_chat.assert_not_awaited()
