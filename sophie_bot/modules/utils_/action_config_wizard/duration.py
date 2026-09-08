from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from aiogram.types import CallbackQuery, Message
from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ARGS_EXCEPTIONS
from ass_tg.i18n import gettext_ctx
from ass_tg.types import ActionTimeArg
from pydantic import BaseModel
from stfu_tg import Template
from stfu_tg.doc import Element

from sophie_bot.services.i18n import i18n
from sophie_bot.utils.i18n import LazyProxy

from .spec import ActionSetupTryAgainException


def make_duration_setup_confirm(
    data_type: type[BaseModel],
    invalid_duration_text: str | LazyProxy,
) -> Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[BaseModel | None]]:
    async def setup_confirm(
        event: Message | CallbackQuery,
        _data: dict[str, Any],
    ) -> BaseModel:
        if isinstance(event, CallbackQuery):
            raise TypeError("This handlers setup_confirm can only be used with messages")

        raw_text = event.text or ""
        try:
            field_name = next(iter(data_type.model_fields))
        except StopIteration:
            raise ValueError("data_type must define at least one model field")

        if raw_text == "0":
            alias = data_type.model_fields[field_name].alias or field_name
            return data_type(**{alias: None})

        try:
            gettext_ctx.set(i18n)
            with i18n.context():
                duration: timedelta = (await ActionTimeArg().parse(raw_text, 0, ArgEntities([])))[1]
        except ARGS_EXCEPTIONS:
            await event.reply(str(invalid_duration_text))
            raise ActionSetupTryAgainException()

        return data_type(**{field_name: duration})

    return setup_confirm


def make_duration_setup_message(
    prompt_text: str | LazyProxy,
) -> Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Element]]:
    async def setup_message(
        _event: Message | CallbackQuery,
        _data: dict[str, Any],
    ) -> Element:
        return Template(prompt_text)

    return setup_message
