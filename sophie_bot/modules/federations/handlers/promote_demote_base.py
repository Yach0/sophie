from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from aiogram.types import Message, User
from ass_tg.types import OptionalArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Bold, Doc, Italic, Template, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.db_exceptions import DBNotFoundException
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _


class FederationPromoteDemoteHandler(FederationCommandHandler):
    action_name: ClassVar[str | LazyProxy]
    owner_only_text: ClassVar[str | LazyProxy]
    user_not_specified_text: ClassVar[str | LazyProxy]
    not_private_user_text: ClassVar[str | LazyProxy]
    success_template: ClassVar[str | LazyProxy]
    log_template: ClassVar[str | LazyProxy]

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        base_args = await super().handler_args(message, data)
        if not message or not is_real_reply(message):
            base_args["user"] = OptionalArg(SophieUserArg(cls.action_name))
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        user_db = await self._resolve_user()
        if not user_db:
            return

        if user_db.type != ChatType.private:
            await self.event.reply(str(self.not_private_user_text))
            return

        if not await FederationPermissionService.validate_federation_owner(federation, self.event.from_user.id):
            await self.event.reply(str(self.owner_only_text))
            return

        await self._execute_action(federation, user_db)

    async def _resolve_user(self) -> ChatModel | None:
        try:
            user_input = get_arg_or_reply_user(self.event, self.data)
        except Exception:  # noqa: BLE001  # arg parsing may raise various errors; reply and abort
            await self.event.reply(str(self.user_not_specified_text))
            return None

        if isinstance(user_input, User):
            try:
                return await ChatModel.find_user(user_input.id)
            except DBNotFoundException:
                await self.event.reply(_("User not found in database."))
                return None

        return user_input

    @abstractmethod
    async def _execute_action(self, federation: Federation, user_db: ChatModel) -> None:
        raise NotImplementedError

    async def _send_success(self, federation: Federation, user_db: ChatModel) -> None:
        await self.event.reply(
            Doc(
                Template(
                    str(self.success_template),
                    user=Bold(UserLink(user_db.tid, user_db.first_name_or_title)),
                    fed_name=Italic(federation.fed_name),
                ),
            ).to_html()
        )

    async def _log_action(self, federation: Federation, user_db: ChatModel) -> None:
        if not self.event.from_user:
            return
        log_text = Template(
            str(self.log_template),
            admin=self.event.from_user.mention_html(),
            user=UserLink(user_db.tid, user_db.first_name_or_title),
            fed_name=federation.fed_name,
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)
