import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.telegram_exceptions import (
    CAN_NOT_BE_DELETED,
    CHAT_WRITE_FORBIDDEN,
    INVALID_BUTTON_URL,
    MSG_NOT_MODIFIED,
    MSG_TEXT_EMPTY,
    MSG_TO_DEL_NOT_FOUND,
    MSG_TOO_LONG,
    NO_TEXT_IN_MSG_TO_EDIT,
    REPLIED_NOT_FOUND,
    REPLY_MESSAGE_INVALID,
)


async def successful_result() -> str:
    return "ok"


async def raises_bad_request(message: str) -> None:
    raise TelegramBadRequest(method=None, message=message)  # type: ignore[arg-type]


async def raises_forbidden() -> None:
    raise TelegramForbiddenError(method=None, message="bot was blocked")  # type: ignore[arg-type]


async def raises_api_error(message: str) -> None:
    raise TelegramAPIError(method=None, message=message)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_common_try_returns_successful_result() -> None:
    assert await common_try(successful_result()) == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        REPLIED_NOT_FOUND,
        REPLY_MESSAGE_INVALID,
        CAN_NOT_BE_DELETED,
        MSG_TO_DEL_NOT_FOUND,
        MSG_TEXT_EMPTY,
        MSG_NOT_MODIFIED,
        NO_TEXT_IN_MSG_TO_EDIT,
        MSG_TOO_LONG,
        INVALID_BUTTON_URL,
        CHAT_WRITE_FORBIDDEN,
    ],
)
async def test_common_try_ignores_expected_bad_request_messages(message: str) -> None:
    assert await common_try(raises_bad_request(message)) is None


@pytest.mark.asyncio
async def test_common_try_uses_reply_not_found_fallback() -> None:
    fallback_called = False

    async def fallback_result() -> str:
        nonlocal fallback_called
        fallback_called = True
        return "fallback"

    def make_fallback() -> object:
        return fallback_result()

    result = await common_try(raises_bad_request(REPLIED_NOT_FOUND), reply_not_found=make_fallback)  # type: ignore[arg-type]

    assert result == "fallback"
    assert fallback_called is True


@pytest.mark.asyncio
async def test_common_try_uses_reply_not_found_fallback_for_reply_message_invalid() -> None:
    fallback_called = False

    async def fallback_result() -> str:
        nonlocal fallback_called
        fallback_called = True
        return "fallback"

    def make_fallback() -> object:
        return fallback_result()

    result = await common_try(
        raises_bad_request(f"Bad Request: {REPLY_MESSAGE_INVALID}"),
        reply_not_found=make_fallback,
    )  # type: ignore[arg-type]

    assert result == "fallback"
    assert fallback_called is True


@pytest.mark.asyncio
async def test_common_try_reraises_unknown_bad_request() -> None:
    with pytest.raises(TelegramBadRequest):
        await common_try(raises_bad_request("unexpected bad request"))


@pytest.mark.asyncio
async def test_common_try_ignores_configured_telegram_exceptions() -> None:
    assert await common_try(raises_forbidden()) is None


@pytest.mark.asyncio
async def test_common_try_reraises_migrate_exception_with_ignored_warning() -> None:
    async def raises_migrate() -> None:
        raise TelegramMigrateToChat(method=None, message="migrate", migrate_to_chat_id=-100123)  # type: ignore[arg-type]

    assert await common_try(raises_migrate()) is None


@pytest.mark.asyncio
async def test_common_try_reraises_telegram_api_timeout() -> None:
    with pytest.raises(TelegramAPIError):
        await common_try(raises_api_error("request timeout"))


@pytest.mark.asyncio
async def test_common_try_reraises_other_telegram_api_errors() -> None:
    with pytest.raises(TelegramAPIError):
        await common_try(raises_api_error("internal server error"))
