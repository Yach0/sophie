from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from aiogram.types import Message
from stfu_tg import BlockQuote, Bold, Code, Doc, Italic, KeyValue, Section, Title

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.op_debug_feature_request import OpDebugFeatureRequestModel
from sophie_bot.db.models.op_debug_snapshot import OpDebugSnapshotModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_summary_model
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.op.json_schemas.op_debug_ai_summary import OpDebugAISummary
from sophie_bot.services.redis import aredis
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, get_default_value, is_enabled, list_all
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.versions import SOPHIE_BRANCH, SOPHIE_COMMIT, SOPHIE_VERSION

_MESSAGE_HISTORY_LIMIT = 35
_MESSAGE_TEXT_LIMIT = 200
_TELEGRAM_TEXT_LIMIT = 4000
_ERROR_SIGNATURE_PREFIX = "sophie:err:sig:"
_SOPHIE_KEY_PREFIX = "sophie:*"
_SECTION_CHUNK_LIMIT = 3000


def _decode_redis_value(value: bytes | str | int | float) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _format_timestamp(timestamp: float | int | str | None) -> str:
    if timestamp is None:
        return str(l_("unknown"))

    try:
        timestamp_value = float(timestamp)
    except ValueError:
        return str(timestamp)

    return datetime.fromtimestamp(timestamp_value, tz=timezone.utc).isoformat()


def _format_cached_message_timestamp(created_at: datetime | None) -> str:
    if created_at is None:
        return str(l_("unknown"))
    return created_at.astimezone(timezone.utc).isoformat()


def _truncate_text(text: str) -> str:
    if len(text) <= _MESSAGE_TEXT_LIMIT:
        return text
    return f"{text[:_MESSAGE_TEXT_LIMIT]}..."


def _get_sender_summary(message: Message) -> str:
    sender = message.from_user or message.sender_chat
    if sender is None:
        return str(l_("unknown"))

    sender_name = (
        getattr(sender, "full_name", None) or getattr(sender, "title", None) or getattr(sender, "username", None)
    )
    sender_id = getattr(sender, "id", str(l_("unknown")))
    if sender_name is None:
        return str(sender_id)
    return f"{sender_name} ({sender_id})"


def _get_sentry_organization_url() -> str | None:
    if CONFIG.sentry_url is None:
        return None

    sentry_url = str(CONFIG.sentry_url)
    parsed_url = urlsplit(sentry_url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    organization_path = f"/{path_parts[0]}" if path_parts else ""
    return urlunsplit((parsed_url.scheme, parsed_url.netloc, organization_path, "", ""))


def _redis_key_group(raw_key: bytes | str) -> str:
    key = raw_key.decode(errors="replace") if isinstance(raw_key, bytes) else raw_key
    key_parts = key.split(":")
    return ":".join(key_parts[:3]) if len(key_parts) >= 3 else key


def _decode_hash(raw_hash: Mapping[bytes | str, bytes | str | int | float]) -> dict[str, str]:
    return {_decode_redis_value(key): _decode_redis_value(value) for key, value in raw_hash.items()}


def _build_error_signature_line(signature_key: str, signature_data: Mapping[str, str]) -> str:
    signature_id = signature_key.removeprefix(_ERROR_SIGNATURE_PREFIX)
    last_seen_at = _format_timestamp(signature_data.get("last_seen_at"))
    last_allowed_at = _format_timestamp(signature_data.get("last_allowed_at"))
    step = signature_data.get("step", str(l_("unknown")))
    return (
        f"{signature_id[:12]}: {l_('last seen')}={last_seen_at}, "
        f"{l_('last allowed')}={last_allowed_at}, {l_('step')}={step}"
    )


def _build_doc(sections: list[Section]) -> Doc:
    doc = Doc(Title(Bold(l_("🔧 Operator Debug"))))
    for section in sections:
        doc += section
    return doc


def _split_sections(sections: list[Section]) -> list[Doc]:
    docs: list[Doc] = []
    current_sections: list[Section] = []

    for section in sections:
        candidate_sections = [*current_sections, section]
        candidate_doc = _build_doc(candidate_sections)
        if len(str(candidate_doc)) > _TELEGRAM_TEXT_LIMIT and current_sections:
            docs.append(_build_doc(current_sections))
            current_sections = [section]
        else:
            current_sections = candidate_sections

    docs.append(_build_doc(current_sections))
    return docs


def _build_blockquote_sections(lines: list[str], title: object, empty_text: object) -> list[Section]:
    if not lines:
        return [Section(BlockQuote(empty_text), title=title)]

    sections: list[Section] = []
    current_lines: list[str] = []

    for line in lines:
        candidate_lines = [*current_lines, line]
        if len("\n".join(candidate_lines)) > _SECTION_CHUNK_LIMIT and current_lines:
            sections.append(Section(BlockQuote("\n".join(current_lines)), title=title))
            current_lines = [line]
        else:
            current_lines = candidate_lines

    sections.append(Section(BlockQuote("\n".join(current_lines)), title=title))
    return sections


def _extract_reply_context(message: Message) -> list[str]:
    lines: list[str] = []
    reply_to = message.reply_to_message
    if reply_to is not None:
        text = reply_to.text or reply_to.caption or ""
        sender = _get_sender_summary(reply_to)
        lines.append(f"[{l_('reply to')}] {sender}: {_truncate_text(text)}")
    return lines


async def _collect_chat_history(chat_id: int) -> tuple[list[Section], list[dict[str, Any]]]:
    messages: tuple[MessageType, ...] = await get_cached_messages(chat_id, limit=_MESSAGE_HISTORY_LIMIT)
    lines: list[str] = []
    history_data: list[dict[str, Any]] = []

    for msg in messages:
        role = str(l_("bot")) if msg.user_id == CONFIG.bot_id else str(l_("user"))
        sender_name = msg.username or str(msg.user_id)
        timestamp = _format_cached_message_timestamp(msg.created_at)
        lines.append(f"{role} [{timestamp}] {sender_name}: {_truncate_text(msg.text)}")
        history_data.append(
            {
                "user_id": msg.user_id,
                "username": msg.username,
                "text": _truncate_text(msg.text),
                "role": "bot" if msg.user_id == CONFIG.bot_id else "user",
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        )

    if not lines:
        return (
            [
                Section(
                    Italic(l_("No cached messages found. Chat history is populated when AI chatbot is active.")),
                    title=l_("Chat History"),
                )
            ],
            history_data,
        )
    return (
        _build_blockquote_sections(
            lines,
            l_("Chat History"),
            l_("No cached messages found."),
        ),
        history_data,
    )


async def _collect_operator_notes(message: Message) -> tuple[list[Section], list[str]]:
    lines: list[str] = []
    notes_data: list[str] = []
    command_text = message.text or ""
    parts = command_text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        lines.append(f"[{l_('command args')}] {parts[1].strip()}")
        notes_data.append(parts[1].strip())
    reply_lines = _extract_reply_context(message)
    lines.extend(reply_lines)
    if reply_lines:
        notes_data.extend(reply_lines)
    if not lines:
        lines.append(str(l_("No additional notes provided.")))
    return (
        _build_blockquote_sections(
            lines,
            l_("Operator Notes"),
            l_("No additional notes provided."),
        ),
        notes_data,
    )


async def _collect_error_backoff() -> tuple[Section, dict[str, Any]]:
    raw_keys = await aredis.keys(f"{_ERROR_SIGNATURE_PREFIX}*")
    signature_rows: list[tuple[float, str]] = []
    signature_data_list: list[dict[str, Any]] = []

    for raw_key in raw_keys:
        signature_key = _decode_redis_value(raw_key)
        raw_hash = cast(Mapping[bytes | str, bytes | str | int | float], await aredis.hgetall(signature_key))
        signature_data = _decode_hash(raw_hash)
        last_seen_at = signature_data.get("last_seen_at")
        try:
            sort_timestamp = float(last_seen_at) if last_seen_at is not None else 0.0
        except ValueError:
            sort_timestamp = 0.0
        signature_rows.append((sort_timestamp, _build_error_signature_line(signature_key, signature_data)))
        signature_data_list.append(
            {
                "signature_id": signature_key.removeprefix(_ERROR_SIGNATURE_PREFIX),
                "last_seen_at": signature_data.get("last_seen_at"),
                "last_allowed_at": signature_data.get("last_allowed_at"),
                "step": signature_data.get("step"),
            }
        )

    signature_rows.sort(reverse=True)
    sentry_url = _get_sentry_organization_url()
    recent_lines = [line for _, line in signature_rows[:10]]

    section_items: list[object] = [
        KeyValue(l_("Active error signatures"), Code(len(signature_rows))),
    ]
    if sentry_url is not None:
        section_items.append(KeyValue(l_("Sentry organization"), Code(sentry_url)))

    section_items.append(
        BlockQuote("\n".join(recent_lines) if recent_lines else l_("No active error signatures found."))
    )

    backoff_data: dict[str, Any] = {
        "active_count": len(signature_rows),
        "sentry_org_url": sentry_url,
        "recent_signatures": sorted(
            signature_data_list, key=lambda sig: float(sig.get("last_seen_at") or 0), reverse=True
        )[:10],
    }
    return Section(*section_items, title=l_("Error Backoff")), backoff_data


async def _collect_feature_flags() -> tuple[Section, dict[str, Any]]:
    states = await list_all()
    lines: list[str] = []
    flags_data: dict[str, Any] = {}

    overridden_count = 0
    for feature in FEATURE_FLAGS:
        enabled = states[feature]
        default_enabled = get_default_value(feature)
        marker = "✦" if enabled != default_enabled else " "
        if enabled != default_enabled:
            overridden_count += 1
        lines.append(f"{marker} {feature}: {str(enabled).lower()} ({l_('default')}: {str(default_enabled).lower()})")
        flags_data[feature] = {"enabled": enabled, "default": default_enabled, "overridden": enabled != default_enabled}

    return (
        Section(
            Italic(l_("{count} flag(s) differ from defaults (marked with ✦)").format(count=overridden_count)),
            BlockQuote("\n".join(lines)),
            title=l_("Feature Flags"),
        ),
        flags_data,
    )


async def _collect_redis_health() -> tuple[Section, dict[str, Any]]:
    ping_result = await aredis.ping()
    db_size = await aredis.dbsize()
    raw_keys = await aredis.keys(_SOPHIE_KEY_PREFIX)
    key_groups = Counter(_redis_key_group(raw_key) for raw_key in raw_keys)
    group_lines = [f"{group}: {count}" for group, count in sorted(key_groups.items())]

    health_data: dict[str, Any] = {
        "ping": _decode_redis_value(ping_result),
        "db_keys": db_size,
        "sophie_keys": len(raw_keys),
        "key_groups": dict(sorted(key_groups.items())),
    }

    return (
        Section(
            KeyValue(l_("Ping"), Code(ping_result)),
            KeyValue(l_("Database keys"), Code(db_size)),
            KeyValue(l_("Sophie keys"), Code(len(raw_keys))),
            BlockQuote("\n".join(group_lines) if group_lines else l_("No sophie:* keys found.")),
            title=l_("Redis Health"),
        ),
        health_data,
    )


def _collect_system_context() -> tuple[Section, dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    unix_time = int(time.time())
    context_data: dict[str, Any] = {
        "version": SOPHIE_VERSION,
        "commit": SOPHIE_COMMIT,
        "branch": SOPHIE_BRANCH,
        "instance_name": CONFIG.instance_name,
        "environment": CONFIG.environment,
        "debug_mode": CONFIG.debug_mode,
        "sophie_mode": SOPHIE_MODE,
        "utc_time": now_utc.isoformat(),
        "unix_time": unix_time,
    }
    return (
        Section(
            KeyValue(l_("Current time (UTC)"), Code(now_utc.isoformat())),
            KeyValue(l_("Unix time"), Code(unix_time)),
            KeyValue(l_("Version"), Italic(SOPHIE_VERSION)),
            KeyValue(l_("Commit"), Code(SOPHIE_COMMIT)),
            KeyValue(l_("Branch"), Italic(SOPHIE_BRANCH)),
            KeyValue(l_("Instance"), Code(CONFIG.instance_name)),
            KeyValue(l_("Environment"), Code(CONFIG.environment)),
            KeyValue(l_("Debug mode"), Code(CONFIG.debug_mode)),
            KeyValue(l_("SOPHIE_MODE"), Code(SOPHIE_MODE)),
            title=l_("System Context"),
        ),
        context_data,
    )


def _collect_chat_context(message: Message) -> tuple[Section, dict[str, Any], int, str]:
    from_user = message.from_user
    user_summary = l_("unknown")
    operator_id = 0
    operator_name = "unknown"
    if from_user is not None:
        user_summary = f"{from_user.full_name} ({from_user.id})"
        operator_id = from_user.id
        operator_name = from_user.full_name

    chat_data: dict[str, Any] = {
        "chat_id": message.chat.id,
        "chat_type": message.chat.type,
        "chat_title": message.chat.title,
    }
    return (
        Section(
            KeyValue(l_("Chat ID"), Code(message.chat.id)),
            KeyValue(l_("Chat type"), Code(message.chat.type)),
            KeyValue(l_("Chat title"), Code(message.chat.title or l_("unknown"))),
            KeyValue(l_("Invoked by"), Code(user_summary)),
            title=l_("Current Chat"),
        ),
        chat_data,
        operator_id,
        operator_name,
    )


def _build_ai_summary_prompt(
    notes_data: list[str],
    history_data: list[dict[str, Any]],
    backoff_data: dict[str, Any],
    system_data: dict[str, Any],
) -> str:
    """Build the prompt for AI to classify and summarize the debug report."""
    context_parts: list[str] = []

    context_parts.append("## Operator Notes\n" + "\n".join(notes_data))

    if backoff_data.get("active_count", 0) > 0:
        context_parts.append(
            "## Active Error Signatures\n" + json.dumps(backoff_data.get("recent_signatures", []), indent=2)
        )

    if history_data:
        recent_messages = history_data[-10:]
        formatted_history = "\n".join(
            f"[{msg.get('role', 'unknown')}] {msg.get('username', '?')}: {msg.get('text', '')}"
            for msg in recent_messages
        )
        context_parts.append(f"## Recent Chat History\n{formatted_history}")

    context_parts.append(
        f"## System Info\nVersion: {system_data.get('version')}, Branch: {system_data.get('branch')}, "
        f"Mode: {system_data.get('sophie_mode')}, Environment: {system_data.get('environment')}"
    )

    full_context = "\n\n".join(context_parts)

    instructions = (
        "Analyze the following debug report context submitted by a bot operator. "
        "Determine whether this is a BUG REPORT (reporting an error, unexpected behavior, or malfunction) "
        "or a FEATURE REQUEST (requesting new functionality, improvements, or changes).\n\n"
        "Provide a structured summary including:\n"
        "- A clear, concise title\n"
        "- A detailed summary of the issue or request\n"
        "- Severity estimate (low/medium/high/critical)\n"
        "- Key points as bullet items\n"
        "- A suggested next action for the operator\n\n"
        "If error signatures are present and notes reference errors or problems, classify as a bug report.\n"
        "If notes describe desired new features, improvements, or configuration changes, classify as a feature request.\n\n"
        f"Debug Report Context:\n{full_context}"
    )

    return instructions


def _build_ai_summary_sections(ai_summary: OpDebugAISummary) -> list[Section]:
    """Build STFU sections from the AI summary result."""
    report_type_label = l_("🐛 Bug Report") if ai_summary.report_type.value == "bug" else l_("✨ Feature Request")

    key_points_text = "\n".join(f"• {point}" for point in ai_summary.key_points) if ai_summary.key_points else "-"

    summary_items: list[object] = [
        KeyValue(l_("Type"), Bold(report_type_label)),
        KeyValue(l_("Title"), Italic(ai_summary.title)),
        KeyValue(l_("Severity"), Code(ai_summary.severity)),
    ]

    summary_items.append(BlockQuote(ai_summary.summary))

    summary_section = Section(*summary_items, title=l_("AI Summary"))

    details_items: list[object] = [
        BlockQuote(key_points_text),
    ]
    if ai_summary.suggested_action:
        details_items.append(BlockQuote(ai_summary.suggested_action))

    details_section = Section(
        *details_items,
        title=l_("AI Analysis"),
    )

    return [summary_section, details_section]


async def _generate_ai_summary(
    notes_data: list[str],
    history_data: list[dict[str, Any]],
    backoff_data: dict[str, Any],
    system_data: dict[str, Any],
    chat_iid,
    chat_tid: int,
) -> OpDebugAISummary | None:
    """Call AI to generate a structured summary of the debug report."""
    history = AIMessageHistory()
    history.add_system(
        "You are a debugging assistant for SophieBot, a Telegram moderation bot. Analyze operator debug reports and provide structured summaries."
    )

    prompt_text = _build_ai_summary_prompt(notes_data, history_data, backoff_data, system_data)
    history.add_custom(prompt_text, name="OperatorDebug")

    model = await get_chat_summary_model(chat_iid, chat_tid=chat_tid)
    result = await run_structured_task(
        AIStructuredTask(
            output_type=OpDebugAISummary,
            feature=AI_FEATURE_CHATBOT,
        ),
        model,
        history,
        chat_iid=chat_iid,
        chat_tid=chat_tid,
    )
    return result.output


async def _save_feature_request(
    ai_summary: OpDebugAISummary,
    chat_id: int,
    operator_id: int,
    operator_name: str,
    snapshot_id: str | None,
) -> OpDebugFeatureRequestModel:
    """Save an AI-classified feature request to the database."""
    feature_request = OpDebugFeatureRequestModel(
        chat_id=chat_id,
        operator_id=operator_id,
        operator_name=operator_name,
        title=ai_summary.title,
        summary=ai_summary.summary,
        severity=ai_summary.severity,
        key_points=ai_summary.key_points,
        suggested_action=ai_summary.suggested_action,
        snapshot_id=snapshot_id,
    )
    await feature_request.insert()
    return feature_request


async def _collect_debug_context(
    message: Message,
    *,
    force_ai: bool = False,
) -> None:
    """Shared flow: collect diagnostic context, persist snapshot, optionally run AI, reply."""
    system_section, system_data = _collect_system_context()
    chat_section, chat_data, operator_id, operator_name = _collect_chat_context(message)
    redis_section, redis_data = await _collect_redis_health()
    backoff_section, backoff_data = await _collect_error_backoff()
    flags_section, flags_data = await _collect_feature_flags()
    history_sections, history_data = await _collect_chat_history(message.chat.id)
    notes_sections, notes_data = await _collect_operator_notes(message)

    snapshot = OpDebugSnapshotModel(
        chat_id=message.chat.id,
        operator_id=operator_id,
        operator_name=operator_name,
        system_context=system_data,
        chat_context=chat_data,
        redis_health=redis_data,
        error_backoff=backoff_data,
        feature_flags=flags_data,
        chat_history=history_data,
        operator_notes=notes_data,
    )
    await snapshot.insert()

    sections = [system_section, chat_section, redis_section, backoff_section, flags_section]
    sections[2:2] = history_sections
    sections.extend(notes_sections)

    run_ai = force_ai or await is_enabled("op_debug_ai_summarization", chat_tid=message.chat.id)
    if run_ai:
        chat_model = await ChatModel.get_by_tid(message.chat.id)
        if chat_model is not None:
            ai_summary = await _generate_ai_summary(
                notes_data, history_data, backoff_data, system_data, chat_model.iid, message.chat.id
            )
            if ai_summary is not None:
                sections.extend(_build_ai_summary_sections(ai_summary))

                if ai_summary.report_type.value == "feature_request":
                    await _save_feature_request(
                        ai_summary,
                        chat_id=message.chat.id,
                        operator_id=operator_id,
                        operator_name=operator_name,
                        snapshot_id=str(snapshot.id),
                    )

    for doc in _split_sections(sections):
        await message.reply(str(doc))


@flags.help(description=l_("Collect diagnostic context for debugging bot issues (private chat only)."))
class OpDebugHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple:
        from aiogram.enums import ChatType
        from sophie_bot.filters.chat_status import ChatTypeFilter

        return (CMDFilter("op_debug"), IsOP(True), ChatTypeFilter(ChatType.PRIVATE))

    async def handle(self) -> None:
        await _collect_debug_context(self.event)


@flags.help(description=l_("Collect diagnostic context with AI summarization (requires feature flag)."))
class OpDebugAISummaryHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CMDFilter, IsOP, FeatureFlagFilter]:
        return (
            CMDFilter("op_debug_ai"),
            IsOP(True),
            FeatureFlagFilter("op_debug_ai_summarization"),
        )

    async def handle(self) -> None:
        await _collect_debug_context(self.event, force_ai=True)
