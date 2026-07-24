from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message, TelegramObject
from stfu_tg import Doc, Section, VList

from sophie_bot.config import CONFIG
from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.utils_.admin import check_user_admin_permissions
from sophie_bot.modules.utils_.anonymous_admin import normalize_admin_title, resolve_anonymous_admin_candidates
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


@dataclass
class UserRestricting(Filter):
    admin: bool = False
    user_owner: bool = False
    can_post_messages: bool = False
    can_edit_messages: bool = False
    can_delete_messages: bool = False
    can_restrict_members: bool = False
    can_promote_members: bool = False
    can_change_info: bool = False
    can_invite_users: bool = False
    can_pin_messages: bool = False

    ARGUMENTS: dict[str, str] = field(
        default_factory=lambda: {
            "user_admin": "admin",
            "user_owner": "user_owner",
            "user_can_post_messages": "can_post_messages",
            "user_can_edit_messages": "can_edit_messages",
            "user_can_delete_messages": "can_delete_messages",
            "user_can_restrict_members": "can_restrict_members",
            "user_can_promote_members": "can_promote_members",
            "user_can_change_info": "can_change_info",
            "user_can_invite_users": "can_invite_users",
            "user_can_pin_messages": "can_pin_messages",
        },
        repr=False,
    )
    PAYLOAD_ARGUMENT_NAME: str = field(default="user_member", repr=False)

    required_permissions: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.required_permissions = [
            arg for arg in self.ARGUMENTS.values() if arg not in {"admin", "user_owner"} and getattr(self, arg)
        ]

    @classmethod
    def validate(cls, full_config: dict[str, Any]) -> dict[str, Any]:
        config: dict[str, Any] = {}
        for alias, argument in cls.ARGUMENTS.items():
            if alias in full_config:
                config[argument] = full_config.pop(alias)
        return config

    async def __call__(
        self,
        event: TelegramObject,
        connection: ChatConnection | None = None,
        user_db: ChatModel | None = None,
    ) -> bool | dict[str, Any]:
        message = self.get_event_message(event)
        if message is None:
            return False

        target = await self.get_target(event, user_db)
        target_tid = target.tid if isinstance(target, ChatModel) else target
        chat_ref: int | ChatModel = connection.db_model if connection else message.chat.id
        chat_tid = chat_ref.tid if isinstance(chat_ref, ChatModel) else chat_ref
        is_connected = connection.is_connected if connection else False
        payload: dict[str, Any] = {}

        # Skip if in PM and not connected to the chat
        if not is_connected and message.chat.type == "private":
            log.debug("Admin rights: Private message without connection")
            return True

        if is_connected:
            log.debug("Admin rights: Connection to the chat detected")

        anonymous_resolution = await self.resolve_anonymous_admin_permissions(
            event=event,
            chat_tid=chat_tid,
            user_tid=target_tid,
            connection=connection,
            user_db=user_db,
        )
        if anonymous_resolution:
            if anonymous_resolution.permission_check is not True:
                if not anonymous_resolution.already_notified:
                    if self.user_owner:
                        await self.no_owner_msg(event)
                    else:
                        await self.no_rights_msg(event, anonymous_resolution.permission_check, target_tid)
                raise SkipHandler

            if anonymous_resolution.resolved_user_db:
                payload["user_db"] = anonymous_resolution.resolved_user_db

            return payload or True

        if self.user_owner:
            is_owner = await check_user_admin_permissions(chat_ref, target, require_creator=True)
            if is_owner is not True:
                await self.no_owner_msg(event)
                raise SkipHandler
            return True

        check = await check_user_admin_permissions(chat_ref, target, self.required_permissions or None)
        if check is not True:
            # check = missing permission in this scope
            await self.no_rights_msg(event, check, target_tid)
            raise SkipHandler

        return payload or True

    async def resolve_anonymous_admin_permissions(
        self,
        event: TelegramObject,
        chat_tid: int,
        user_tid: int,
        connection: ChatConnection | None,
        user_db: ChatModel | None,
    ) -> AnonymousResolution | None:
        if user_tid != TELEGRAM_ANONYMOUS_ADMIN_BOT_ID:
            return None

        message = self._resolve_message(event)
        if not hasattr(message, "sender_chat") or not hasattr(message, "author_signature"):
            return None

        sender_chat = getattr(message, "sender_chat", None)
        if not sender_chat or sender_chat.id != chat_tid:
            return None

        title = normalize_admin_title(getattr(message, "author_signature", None))
        if not title:
            await self.no_anon_title_msg(event)
            return AnonymousResolution(permission_check=False, resolved_user_db=None, already_notified=True)

        chat_model = connection.db_model if connection else None
        if not chat_model:
            return AnonymousResolution(permission_check=False, resolved_user_db=None, already_notified=False)

        matched_admins = await resolve_anonymous_admin_candidates(chat_model.iid, title)

        if not matched_admins:
            await self.no_anon_title_match_msg(event)
            return AnonymousResolution(permission_check=False, resolved_user_db=None, already_notified=True)

        checks = [
            self.check_member_permissions(member=admin.member, require_creator=self.user_owner)
            for admin in matched_admins
        ]
        if not all(check is True for check in checks):
            await self.no_anon_ambiguous_msg(event)
            return AnonymousResolution(permission_check=False, resolved_user_db=None, already_notified=True)

        if len(matched_admins) == 1:
            resolved_user_db = await matched_admins[0].user.fetch()
            if resolved_user_db:
                return AnonymousResolution(permission_check=True, resolved_user_db=resolved_user_db)

        if user_db:
            return AnonymousResolution(permission_check=True, resolved_user_db=user_db)

        for admin in matched_admins:
            resolved_user_db = await admin.user.fetch()
            if resolved_user_db:
                return AnonymousResolution(permission_check=True, resolved_user_db=resolved_user_db)

        return AnonymousResolution(permission_check=True, resolved_user_db=None)

    def check_member_permissions(
        self,
        member: Any,
        require_creator: bool = False,
    ) -> bool | list[str]:
        if require_creator:
            return getattr(member, "status", None) == ChatMemberStatus.CREATOR

        if getattr(member, "status", None) == ChatMemberStatus.CREATOR:
            return True

        if not self.required_permissions:
            return True

        missing_permissions = []
        for permission in self.required_permissions:
            permission_value = getattr(member, permission, None)
            if permission_value is None or permission_value is False:
                missing_permissions.append(permission)

        return missing_permissions or True

    async def get_target(self, event: TelegramObject, user_db: ChatModel | None) -> int | ChatModel:
        """The entity whose admin rights this filter checks: the sender of the event."""
        from_user = getattr(event, "from_user", None)
        if not from_user:
            raise ValueError("Event must expose a from_user")
        return user_db or from_user.id

    @staticmethod
    def _resolve_message(event: TelegramObject) -> Any:
        """Resolve the actual message from a Telegram event for dynamic reply/answer access."""
        return event.message if isinstance(event, CallbackQuery) else event

    @staticmethod
    def get_event_message(event: TelegramObject) -> Any | None:
        if isinstance(event, CallbackQuery):
            return event.message
        if isinstance(event, Message):
            return event

        # Support message-like test doubles and lightweight event objects.
        if hasattr(event, "chat"):
            return event
        if hasattr(event, "message"):
            return event.message

        return None

    async def _send_doc_reply(self, event: TelegramObject, doc: Doc) -> None:
        """Reply to an event with a Doc, falling back to answer on failure.

        For callback queries the reply is shown as a private alert popup on the
        clicking user's client instead of a chat message, so a non-admin tapping
        an inline button does not spam the whole chat.
        """
        # Callback queries: answer with a private alert popup, not a chat message.
        if isinstance(event, CallbackQuery):
            await event.answer(str(doc), show_alert=True)
            return

        actual_message = self._resolve_message(event)

        async def answer() -> Any:
            return await actual_message.answer(str(doc))

        if hasattr(actual_message, "reply"):
            await common_try(actual_message.reply(str(doc)), reply_not_found=answer)
        elif hasattr(actual_message, "answer"):
            await answer()

    async def no_rights_msg(
        self, event: TelegramObject, required_permissions: bool | list[str], target_tid: int
    ) -> None:
        is_bot = target_tid == CONFIG.bot_id

        if not isinstance(required_permissions, bool):
            missing_perms = [p.replace("can_", "").replace("_", " ") for p in required_permissions]
            text = (
                _("I don't have the following permissions to do this:")
                if is_bot
                else _("You don't have the following permissions to do this:")
            )
            doc = Doc(Section(text, VList(*missing_perms)))
        else:
            text = (
                _("I must be an administrator to use this command.")
                if is_bot
                else _("You must be an administrator to use this command.")
            )
            doc = Doc(text)

        await self._send_doc_reply(event, doc)

    async def no_anon_title_msg(self, event: TelegramObject) -> None:
        doc = Doc(_("Anonymous admin must have a custom admin title to use this command."))
        await self._send_doc_reply(event, doc)

    async def no_anon_title_match_msg(self, event: TelegramObject) -> None:
        doc = Doc(_("Could not resolve this anonymous admin title. Refresh admin cache or use a unique title."))
        await self._send_doc_reply(event, doc)

    async def no_anon_ambiguous_msg(self, event: TelegramObject) -> None:
        doc = Doc(
            _(
                "Multiple anonymous admins share this title, and not all of them can use this command. "
                "Use a unique title."
            )
        )
        await self._send_doc_reply(event, doc)

    async def no_owner_msg(self, event: TelegramObject) -> None:
        doc = Doc(_("You must be the chat creator to use this command."))
        await self._send_doc_reply(event, doc)


@dataclass
class AnonymousResolution:
    permission_check: bool | list[str]
    resolved_user_db: ChatModel | None
    already_notified: bool = False


class BotHasPermissions(UserRestricting):
    # Read-only override of the base dataclass field on a non-dataclass subclass: ClassVar would
    # clash with the base instance field and field() would break the plain-dict access below.
    ARGUMENTS = {  # noqa: RUF012
        "bot_admin": "admin",
        "bot_can_post_messages": "can_post_messages",
        "bot_can_edit_messages": "can_edit_messages",
        "bot_can_delete_messages": "can_delete_messages",
        "bot_can_restrict_members": "can_restrict_members",
        "bot_can_promote_members": "can_promote_members",
        "bot_can_change_info": "can_change_info",
        "bot_can_invite_users": "can_invite_users",
        "bot_can_pin_messages": "can_pin_messages",
    }
    PAYLOAD_ARGUMENT_NAME = "bot_member"

    async def get_target(self, event: TelegramObject, user_db: ChatModel | None) -> int | ChatModel:
        return CONFIG.bot_id
