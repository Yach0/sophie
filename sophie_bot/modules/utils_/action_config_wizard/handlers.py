from __future__ import annotations

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.modules.utils_.wizard import WizardCallback
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler

from .wizard import ActionWizard


class ActionWizardStartHandler(SophieMessageHandler):
    wizard: ActionWizard

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> None:
        await self.wizard.start(self)


class ActionWizardCallbackHandler(SophieCallbackQueryHandler):
    wizard: ActionWizard

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> None:
        callback: WizardCallback = self.data["callback_data"]
        await self.wizard.handle_callback(self, callback)


class ActionWizardInputHandler(SophieMessageHandler):
    wizard: ActionWizard

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> None:
        await self.wizard.handle_input(self)


class ActionWizardInputCleanupHandler(SophieMessageHandler):
    wizard: ActionWizard

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> None:
        await self.wizard.reject_input(self)
