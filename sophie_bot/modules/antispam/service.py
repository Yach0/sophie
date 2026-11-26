from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram.enums import MessageEntityType
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from beanie import PydanticObjectId
from beanie.odm.queries.update import UpdateResponse
from beanie.operators import Inc, Set
from stfu_tg import Doc, Italic, KeyValue, Section, Template, Url, UserLink

from sophie_bot.db.models.antispam import (
    AntispamConfirmedSpamModel,
    AntispamExclusionModel,
    AntispamIncidentModel,
    AntispamIncidentResolution,
    AntispamMode,
    AntispamPolicy,
    AntispamSettingsModel,
)
from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.modules.antispam.callbacks import AntispamIncidentCallback, IncidentOperation
from sophie_bot.modules.antispam.provider import (
    AntispamProvider,
    ProtectionUnavailable,
    ProtectionVerdict,
    ProviderCapability,
    ProviderOrigin,
    ThreatCategory,
)
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.telegram_exceptions import REPLY_MESSAGE_INVALID
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log

CATEGORY_LABELS = {
    ThreatCategory.SPAM: l_("Spam"),
    ThreatCategory.SCAM: l_("Scam"),
    ThreatCategory.PHISHING: l_("Phishing"),
    ThreatCategory.IMPERSONATION: l_("Impersonation"),
    ThreatCategory.MALWARE: l_("Malware"),
    ThreatCategory.OTHER: l_("Other"),
}
_PERMANENT_DELETE_ERRORS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
_TRANSIENT_DELETE_ERRORS = (TelegramNetworkError, TelegramRetryAfter, TimeoutError)


@dataclass(frozen=True, slots=True)
class SourceMatch:
    provider: AntispamProvider
    verdict: ProtectionVerdict
    check: ProviderCapability


@dataclass(frozen=True, slots=True)
class IncidentResolutionResult:
    status: str
    incident: AntispamIncidentModel | None = None


async def available_verdict(result: Awaitable[ProtectionVerdict]) -> ProtectionVerdict | None:
    try:
        return await result
    except ProtectionUnavailable:
        return None


def describe_verdict(verdict: ProtectionVerdict) -> Doc:
    doc = Doc()
    if verdict.reason:
        doc += Template(_("Reason: {reason}"), reason=verdict.reason[:400])
    if verdict.categories:
        doc += Template(
            _("Categories: {categories}"),
            categories=", ".join(str(CATEGORY_LABELS[category]) for category in verdict.categories),
        )
    if verdict.reference_url:
        doc += Url(_("Source and appeals"), verdict.reference_url)
    return doc


def _format_info_verdict(provider: AntispamProvider, verdict: ProtectionVerdict | None) -> Template | str:
    if verdict is None:
        if provider.attribution_url:
            return Template(
                "{status} ({link})",
                status=_("Unavailable"),
                link=Url(_("Source"), provider.attribution_url),
            )
        return _("Unavailable")
    if not verdict.matched:
        if verdict.reference_url:
            return Template(
                "{status} ({link})",
                status=_("Clean"),
                link=Url(_("Source"), verdict.reference_url),
            )
        return _("Clean")
    detail_parts: list[str] = []
    if verdict.reason:
        detail_parts.append(verdict.reason[:400])
    if verdict.categories:
        detail_parts.append(", ".join(str(CATEGORY_LABELS[category]) for category in verdict.categories))
    detail_text = " — ".join(detail_parts)
    if detail_text and verdict.reference_url:
        return Template(
            "{status}: {details} ({link})",
            status=_("Flagged"),
            details=detail_text,
            link=Url(_("Source and appeals"), verdict.reference_url),
        )
    if detail_text:
        return Template("{status}: {details}", status=_("Flagged"), details=detail_text)
    if verdict.reference_url:
        return Template(
            "{status} ({link})",
            status=_("Flagged"),
            link=Url(_("Source and appeals"), verdict.reference_url),
        )
    return _("Flagged")


async def build_user_antispam_section(
    user_tid: int,
    chat_tid: int,
    services: ApplicationServices,
) -> Section | None:
    registry = services.antispam
    if registry is None or not await is_enabled("antispam", chat_tid=chat_tid, redis=services.redis):
        return None
    providers = await registry.active(chat_tid, ProviderCapability.USER_LOOKUP)
    if not providers:
        return None
    results = await gather(*(available_verdict(provider.check_user(user_tid)) for provider in providers))
    entries = [
        KeyValue(provider.display_name, _format_info_verdict(provider, result))
        for provider, result in zip(providers, results, strict=True)
    ]
    return Section(*entries, title=_("Anti-spam"))


def is_trusted_member(membership: UserInGroupModel | None, trusted_after_seconds: int | None) -> bool:
    if membership is None or trusted_after_seconds is None:
        return False
    first_saw = membership.first_saw
    if first_saw.tzinfo is None:
        first_saw = first_saw.replace(tzinfo=UTC)
    return datetime.now(UTC) - first_saw >= timedelta(seconds=trusted_after_seconds)


async def exempt(
    chat: ChatModel,
    user: User,
    services: ApplicationServices,
    *,
    message: Message | None = None,
    membership: UserInGroupModel | None = None,
    trusted_after_seconds: int | None = None,
    joining: bool = False,
) -> bool:
    if user.id == services.bot.id or (message is not None and message.sender_chat is not None):
        return True
    if await is_user_admin(chat.iid, user.id):
        return True
    if (
        joining
        and user.is_bot
        and message
        and message.from_user
        and await is_user_admin(chat.iid, message.from_user.id)
    ):
        return True
    if not joining and is_trusted_member(membership, trusted_after_seconds):
        return True
    if await is_user_group_whitelisted(chat.tid, user.id, redis=services.redis):
        await log_group_whitelist_exemption(chat.tid, user.id, "antispam")
        return True
    return await AntispamExclusionModel.is_excluded(chat.iid, user.id)


async def claim_scan_slot(membership: UserInGroupModel | None, message_id: int, limit: int) -> int | None:
    if membership is None or limit == 0:
        return None
    # Count once per user and message across third-party content scanners.
    updated = await UserInGroupModel.find_one(
        UserInGroupModel.id == membership.id,
        {"$or": [{"antispam_seen_messages": {"$lt": limit}}, {"antispam_seen_messages": {"$exists": False}}]},
        {"$or": [{"antispam_last_message_id": {"$lt": message_id}}, {"antispam_last_message_id": {"$exists": False}}]},
    ).update(
        Inc({UserInGroupModel.antispam_seen_messages: 1}),
        Set({UserInGroupModel.antispam_last_message_id: message_id}),
        response_type=UpdateResponse.NEW_DOCUMENT,
    )
    return updated.antispam_seen_messages if updated else None


def _incident_keyboard(mode: AntispamMode, incident_id: str) -> InlineKeyboardMarkup:
    if mode == AntispamMode.NORMAL:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("Unmute"),
                        callback_data=AntispamIncidentCallback(
                            op=IncidentOperation.UNDO, incident_id=incident_id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text=_("Ban"),
                        callback_data=AntispamIncidentCallback(
                            op=IncidentOperation.BAN, incident_id=incident_id
                        ).pack(),
                    ),
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Unban"),
                    callback_data=AntispamIncidentCallback(op=IncidentOperation.UNDO, incident_id=incident_id).pack(),
                )
            ]
        ]
    )


def _notice_headline(mode: AntispamMode, user: User, match: SourceMatch) -> Template:
    user_link = UserLink(user.id, user.first_name)
    source = match.provider.display_name
    if mode == AntispamMode.STRICT:
        return Template(_("{user} has been banned by {source}."), user=user_link, source=source)
    return Template(_("{user} has been muted by {source}."), user=user_link, source=source)


async def _try_delete_message(services: ApplicationServices, chat_tid: int, message_id: int) -> bool:
    """Return True if the message is deleted or permanently gone; False on transient error."""
    try:
        await services.bot.delete_message(chat_id=chat_tid, message_id=message_id)
        return True
    except _PERMANENT_DELETE_ERRORS:
        log.debug("Skipping permanently undeletable anti-spam message", chat_tid=chat_tid, message_id=message_id)
        return True
    except _TRANSIENT_DELETE_ERRORS as error:
        log.warning(
            "Transient failure deleting anti-spam message",
            chat_tid=chat_tid,
            message_id=message_id,
            error=str(error),
        )
        return False


async def resolve_incident(
    incident_id: str,
    operation: IncidentOperation,
    chat: ChatModel,
    admin: User,
    services: ApplicationServices,
) -> IncidentResolutionResult:
    try:
        object_id = PydanticObjectId(incident_id)
    except (TypeError, ValueError):
        return IncidentResolutionResult(status="not_found")

    existing = await AntispamIncidentModel.find_one(
        AntispamIncidentModel.id == object_id,
        AntispamIncidentModel.chat.id == chat.iid,
    )
    if existing is None:
        return IncidentResolutionResult(status="not_found")
    if existing.resolution is not None:
        return IncidentResolutionResult(status="already_resolved", incident=existing)
    if operation == IncidentOperation.BAN and existing.mode != AntispamMode.NORMAL:
        return IncidentResolutionResult(status="unsupported", incident=existing)

    now = datetime.now(UTC)
    target_resolution = (
        AntispamIncidentResolution.UNDONE if operation == IncidentOperation.UNDO else AntispamIncidentResolution.BANNED
    )
    claimed = await AntispamIncidentModel.find_one(
        AntispamIncidentModel.id == object_id,
        AntispamIncidentModel.chat.id == chat.iid,
        {"resolution": None},
    ).update(
        Set({"resolution": target_resolution.value, "resolved_at": now, "resolved_by_tid": admin.id}),
        response_type=UpdateResponse.NEW_DOCUMENT,
    )
    if claimed is None:
        return IncidentResolutionResult(status="already_resolved", incident=existing)

    if operation == IncidentOperation.UNDO:
        if claimed.mode == AntispamMode.NORMAL:
            await execute_restriction(services.bot, RestrictionAction.UNMUTE, chat.tid, claimed.user_tid)
        elif claimed.mode == AntispamMode.STRICT:
            await execute_restriction(services.bot, RestrictionAction.UNBAN, chat.tid, claimed.user_tid)

        await AntispamExclusionModel.record(
            chat,
            claimed.user_tid,
            source_id=claimed.source_id,
            created_by_tid=admin.id,
            original_message_text=claimed.original_message_text if claimed.source_id == "ussr" else None,
            incident_id=claimed.id,
        )
        await log_event(
            chat,
            claimed.user_tid,
            LogEvent.ANTISPAM_UNDONE,
            {
                "incident_id": str(claimed.id),
                "source_id": claimed.source_id,
                "mode": claimed.mode.value,
                "by_tid": admin.id,
            },
        )
        return IncidentResolutionResult(status="undone", incident=claimed)

    await execute_restriction(services.bot, RestrictionAction.BAN, chat.tid, claimed.user_tid)
    if claimed.original_message_text:
        await AntispamConfirmedSpamModel(
            chat=chat,
            chat_tid=chat.tid,
            user_tid=claimed.user_tid,
            source_id=claimed.source_id,
            message_text=claimed.original_message_text,
            incident_id=claimed.id,
            confirmed_by_tid=admin.id,
            message_timestamp=claimed.created_at,
            confirmed_at=now,
        ).insert()
    if (
        claimed.user_message_id is not None
        and claimed.user_message_deleted_at is None
        and await _try_delete_message(services, chat.tid, claimed.user_message_id)
    ):
        await AntispamIncidentModel.find_one(AntispamIncidentModel.id == claimed.id).update(
            Set({"user_message_deleted_at": now})
        )
    await log_event(
        chat,
        claimed.user_tid,
        LogEvent.USER_BANNED,
        {
            "source": "antispam_escalate",
            "incident_id": str(claimed.id),
            "source_id": claimed.source_id,
            "by_tid": admin.id,
        },
    )
    return IncidentResolutionResult(status="banned", incident=claimed)


async def cleanup_expired_antispam_messages(services: ApplicationServices) -> int:
    """Delete expired anti-spam notices and triggering spam messages (unless undone)."""
    now = datetime.now(UTC)
    due_incidents = await AntispamIncidentModel.find(
        {
            "delete_at": {"$lte": now},
            "$or": [
                {"notice_message_id": {"$ne": None}, "notice_deleted_at": None},
                {
                    "user_message_id": {"$ne": None},
                    "user_message_deleted_at": None,
                    "resolution": {"$ne": AntispamIncidentResolution.UNDONE.value},
                },
            ],
        }
    ).to_list()

    deleted_count = 0
    for incident in due_incidents:
        updates: dict[str, datetime] = {}
        if (
            incident.notice_message_id is not None
            and incident.notice_deleted_at is None
            and await _try_delete_message(services, incident.chat_tid, incident.notice_message_id)
        ):
            updates["notice_deleted_at"] = now
            deleted_count += 1
        if (
            incident.user_message_id is not None
            and incident.user_message_deleted_at is None
            and incident.resolution != AntispamIncidentResolution.UNDONE
            and await _try_delete_message(services, incident.chat_tid, incident.user_message_id)
        ):
            updates["user_message_deleted_at"] = now
            deleted_count += 1
        if updates:
            await AntispamIncidentModel.find_one(AntispamIncidentModel.id == incident.id).update(Set(updates))
    return deleted_count


class AntispamService:
    def __init__(
        self,
        services: ApplicationServices,
        chat: ChatModel,
        policy: AntispamPolicy,
        providers: tuple[AntispamProvider, ...],
    ) -> None:
        self.services = services
        self.chat = chat
        self.policy = policy
        self.providers = providers

    @classmethod
    async def for_chat(cls, services: ApplicationServices, chat: ChatModel) -> AntispamService | None:
        registry = services.antispam
        if registry is None or not await is_enabled("antispam", chat_tid=chat.tid, redis=services.redis):
            return None
        policy = await AntispamSettingsModel.get_policy(chat)
        if policy.mode == AntispamMode.OFF:
            return None
        active = await registry.active(chat.tid)
        providers = tuple(
            provider for provider in active if provider.origin == ProviderOrigin.BUILTIN or policy.third_party_enabled
        )
        return cls(services, chat, policy, providers) if providers else None

    async def _scan_providers(self, message: Message, membership: UserInGroupModel | None) -> list[AntispamProvider]:
        registry = self.services.antispam
        if registry is None:
            return []
        scanners = await registry.active(self.chat.tid, ProviderCapability.CONTENT_SCAN)
        eligible = [provider for provider in self.providers if provider in scanners]
        if not eligible:
            return []

        selected = [provider for provider in eligible if provider.origin == ProviderOrigin.BUILTIN]
        third_party = [provider for provider in eligible if provider.origin == ProviderOrigin.THIRD_PARTY]
        if third_party:
            limit = self.policy.scan_new_user_messages_limit
            if limit is None or await claim_scan_slot(membership, message.message_id, limit) is not None:
                selected.extend(third_party)
        return selected

    async def _evaluate(
        self,
        user_tid: int,
        *,
        message: Message | None,
        membership: UserInGroupModel | None,
        joining: bool,
        join_request: bool,
    ) -> list[SourceMatch]:
        checks: list[tuple[AntispamProvider, ProviderCapability, Awaitable[ProtectionVerdict]]] = []
        text = (message.text or message.caption) if message is not None else None
        if message is not None and not joining and text and text.strip():
            scan_providers = await self._scan_providers(message, membership)
            entities = message.entities if message.text else message.caption_entities
            links = tuple(
                entity.url for entity in entities or () if entity.type == MessageEntityType.TEXT_LINK and entity.url
            )
            checks.extend(
                (
                    provider,
                    ProviderCapability.CONTENT_SCAN,
                    provider.scan_content(text, user_tid, chat_tid=self.chat.tid, links=links),
                )
                for provider in scan_providers
            )
        if not join_request or self.policy.mode == AntispamMode.STRICT:
            checks.extend(
                (provider, ProviderCapability.USER_LOOKUP, provider.check_user(user_tid))
                for provider in self.providers
                if ProviderCapability.USER_LOOKUP in provider.capabilities
            )
        results = await gather(*(available_verdict(check) for *_, check in checks))
        return [
            SourceMatch(provider, verdict, capability)
            for (provider, capability, _check), verdict in zip(checks, results, strict=True)
            if verdict is not None and verdict.matched
        ]

    async def delete_message(self, message: Message) -> bool:
        try:
            return await message.delete()
        except TelegramAPIError:
            log.debug("Could not delete message flagged by anti-spam", chat_tid=self.chat.tid)
            return False

    async def _apply(
        self,
        user_tid: int,
        request: ChatJoinRequest | None,
        *,
        joining: bool,
    ) -> tuple[bool, bool]:
        declined = False
        if request is not None:
            try:
                declined = await request.decline()
            except TelegramAPIError:
                log.debug("Could not decline join request flagged by anti-spam", chat_tid=self.chat.tid)
        if self.policy.mode == AntispamMode.STRICT:
            restricted = (
                await execute_restriction(self.services.bot, RestrictionAction.BAN, self.chat.tid, user_tid)
            ).applied
        else:
            until = timedelta(seconds=self.policy.mute_duration_seconds)
            restricted = (
                await execute_restriction(
                    self.services.bot, RestrictionAction.MUTE, self.chat.tid, user_tid, until_date=until
                )
            ).applied
        applied = restricted or declined
        stopped = (declined or (restricted and self.policy.mode == AntispamMode.STRICT)) if joining else applied
        return applied, stopped

    async def _record_and_notify(
        self,
        match: SourceMatch,
        user: User,
        message: Message | None,
        *,
        joining: bool,
    ) -> None:
        now = datetime.now(UTC)
        delay = int(
            await get_value("antispam_notice_delete_after_seconds", chat_tid=self.chat.tid, redis=self.services.redis)
        )
        delete_at = now + timedelta(seconds=delay) if delay > 0 else None
        user_message_id = message.message_id if message is not None and not joining else None
        original_text = (message.text or message.caption) if message is not None and not joining else None
        incident = AntispamIncidentModel(
            chat=self.chat,
            chat_tid=self.chat.tid,
            user_tid=user.id,
            user_name=user.first_name,
            source_id=match.provider.source_id,
            source_name=match.provider.display_name,
            mode=self.policy.mode,
            user_message_id=user_message_id,
            original_message_text=original_text,
            delete_at=delete_at,
            created_at=now,
        )
        await incident.insert()

        if not self.policy.notify_chat:
            return

        doc = Doc(
            _notice_headline(self.policy.mode, user, match),
            describe_verdict(match.verdict),
        )
        if delay > 0:
            doc += Italic(_("This message will be automatically deleted in about 10 minutes."))

        reply_markup = _incident_keyboard(self.policy.mode, str(incident.id))
        thread_id = message.message_thread_id if message else None
        sent: Message | None = None
        try:
            sent = await self.services.bot.send_message(
                self.chat.tid,
                doc.to_html(),
                message_thread_id=thread_id,
                reply_to_message_id=user_message_id,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as error:
            if user_message_id is not None and REPLY_MESSAGE_INVALID in error.message:
                try:
                    sent = await self.services.bot.send_message(
                        self.chat.tid,
                        doc.to_html(),
                        message_thread_id=thread_id,
                        reply_markup=reply_markup,
                    )
                except TelegramAPIError:
                    log.debug("Could not send anti-spam notice", chat_tid=self.chat.tid)
            else:
                log.debug("Could not send anti-spam notice", chat_tid=self.chat.tid)
        except TelegramAPIError:
            log.debug("Could not send anti-spam notice", chat_tid=self.chat.tid)

        if sent is not None:
            await AntispamIncidentModel.find_one(AntispamIncidentModel.id == incident.id).update(
                Set({"notice_message_id": sent.message_id})
            )

    async def protect(
        self,
        user: User,
        *,
        message: Message | None = None,
        request: ChatJoinRequest | None = None,
        membership: UserInGroupModel | None = None,
        joining: bool = False,
    ) -> bool:
        if await exempt(
            self.chat,
            user,
            self.services,
            message=message,
            membership=membership,
            trusted_after_seconds=self.policy.trusted_after_seconds,
            joining=joining,
        ):
            return False
        matches = await self._evaluate(
            user.id, message=message, membership=membership, joining=joining, join_request=request is not None
        )
        if not matches:
            return False

        applied, stopped = await self._apply(user.id, request, joining=joining)

        for match in matches:
            await log_event(
                self.chat,
                user.id,
                LogEvent.ANTISPAM_MATCH,
                {
                    "source_id": match.provider.source_id,
                    "mode": self.policy.mode.value,
                    "check": match.check.name,
                    "categories": [category.value for category in match.verdict.categories],
                    "message_id": message.message_id if message else None,
                    "join_request": request is not None,
                    "applied": applied,
                },
            )

        if applied:
            await self._record_and_notify(matches[0], user, message, joining=joining)
        return stopped
