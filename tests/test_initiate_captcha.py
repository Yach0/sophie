from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendPhoto

from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import CaptchaDMBlockedError, initiate_captcha


@pytest.mark.asyncio
async def test_initiate_captcha_raises_when_user_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(tid=123456)
    group = SimpleNamespace(iid="group_iid", tid=-100987654321, first_name_or_title="Test Group")
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

    forbidden_error = TelegramForbiddenError(
        method=SendPhoto(chat_id=user.tid, photo="placeholder"),
        message="Forbidden: bot was blocked by the user",
    )
    send_photo = AsyncMock(side_effect=forbidden_error)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.initiate_captcha.bot",
        SimpleNamespace(send_photo=send_photo),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.initiate_captcha.dp",
        SimpleNamespace(fsm=SimpleNamespace(get_context=Mock(return_value=state))),
    )

    with pytest.raises(CaptchaDMBlockedError):
        await initiate_captcha(user, group, is_join_request=True)

    assert send_photo.await_count == 1
    state.set_state.assert_awaited_once_with(WelcomeSecurityFSM.captcha)
    state.update_data.assert_awaited_once()
    assert state.update_data.await_args.kwargs["ws_chat_iid"] == str(group.iid)
    assert state.update_data.await_args.kwargs["ws_is_join_request"] is True
    assert "captcha" in state.update_data.await_args.kwargs
