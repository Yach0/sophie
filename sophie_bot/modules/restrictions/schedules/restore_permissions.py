from __future__ import annotations

from sophie_bot.modules.restrictions.utils.restrictions import restore_expired_permissions
from sophie_bot.services.application import ApplicationServices


class RestorePermissions:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def handle(self) -> None:
        await restore_expired_permissions(self.services.bot)
