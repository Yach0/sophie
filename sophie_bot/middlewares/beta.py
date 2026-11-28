from asyncio import timeout
from collections.abc import Awaitable, Callable
from random import randint
from typing import Any, override

import httpx2
import sentry_sdk
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.db.models import BetaModeModel, ChatModel, GlobalSettings
from sophie_bot.db.models.beta import CurrentMode, PreferredMode
from sophie_bot.utils.logger import log
from sophie_bot.utils.serialization import serialize_bot_default


class BetaMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.session: httpx2.AsyncClient | None = None

    def get_session(self) -> httpx2.AsyncClient:
        if self.session is None:
            self.session = httpx2.AsyncClient(
                timeout=httpx2.Timeout(None, connect=30),
                follow_redirects=True,
                trust_env=False,
            )
        return self.session

    async def close(self) -> None:
        if self.session is not None:
            await self.session.aclose()
            self.session = None

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        context = data["context"]
        chat_db: ChatModel | None = context.event_chat or context.actor

        if chat_db and await self.is_beta(chat_db):
            json_request = self.get_data(event)
            log.debug("Sending request to Beta Sophie...", json_request=json_request)
            return await self.send_request(json_request, CONFIG.proxy_beta_instance_url)

        log.debug("Leaving this request for Stable...")
        return await handler(event, data)

    @staticmethod
    async def is_beta(chat_db: ChatModel) -> bool:
        model = await BetaModeModel.get_by_chat_iid(chat_db.iid)
        # Current mode
        if model and model.mode:
            if model.mode == CurrentMode.beta:
                return True
            if model.mode == CurrentMode.stable:
                return False

        # If it has a preferred mode
        if model:
            # Set the current preferred mode as current
            if model.preferred_mode != PreferredMode.auto:
                await BetaModeModel.set_mode(chat_iid=chat_db.iid, new_mode=CurrentMode[model.preferred_mode.name])

            if model.preferred_mode == PreferredMode.beta:
                return True
            if model.preferred_mode == PreferredMode.stable:
                return False

        # Random
        gs_beta_db = await GlobalSettings.get_by_key("beta_percentage")
        percentage = int(gs_beta_db.value) if gs_beta_db else 0

        if percentage <= 0:
            return False

        new_mode = CurrentMode.beta if randint(0, 100) <= percentage else CurrentMode.stable
        log.debug("Random beta mode generated", chat_id=chat_db.tid, new_mode=new_mode)
        await BetaModeModel.set_mode(chat_iid=chat_db.iid, new_mode=new_mode)

        return new_mode == CurrentMode.beta

    @staticmethod
    def get_data(update: TelegramObject) -> str:
        # Never exclude defaults: discriminator fields (such as rich block 'type') carry
        # their tag as a field default, and dropping them makes the payload unparsable
        # on the receiving instance.
        # `warnings=False`: a Default sentinel never matches its field type, and the fallback
        # replacing it with null is the intended outcome, not a mismatch worth reporting.
        return update.model_dump_json(by_alias=True, exclude_none=True, fallback=serialize_bot_default, warnings=False)

    async def send_request(self, json_request: str, instance_url: str) -> None:
        try:
            session = self.get_session()
            with sentry_sdk.start_span(name="Forward beta update", attributes={"sentry.op": "http.client"}) as span:
                async with (
                    timeout(300),
                    session.stream(
                        "POST",
                        instance_url,
                        content=json_request,
                        headers={"Content-Type": "text/plain; charset=utf-8"},
                    ) as response,
                ):
                    span.set_attribute("http.response.status_code", response.status_code)
                    span.status = "error" if response.status_code >= 400 else "ok"
        except (httpx2.RequestError, httpx2.InvalidURL, TimeoutError) as error:
            log.error(
                "Failed to send request to the second backend.",
                instance_url=instance_url,
                error=error,
            )
