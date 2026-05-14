from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timezone
from itertools import chain

from babel.dates import format_date, format_time
from beanie import PydanticObjectId
from stfu_tg import BlockQuote, Doc, HList, Italic, Template, Title, Url, VList

from sophie_bot.db.models import AIChatSummaryLine, AIChatSummaryModel, AIEnabledModel, ChatModel
from sophie_bot.modules.ai.json_schemas.chat_summary import AIChatSummaryGroup, AIChatSummaryGroups
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_summary_model
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages_between
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate_schema_with_result
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.modules.utils_.scheduler.chat_language import UseChatLanguage
from sophie_bot.modules.utils_.scheduler.for_chats import ForChats
from sophie_bot.services.bot import bot
from sophie_bot.services.sentry_metrics import count_metric
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, is_enabled
from sophie_bot.utils.i18n import get_i18n
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

MIN_TOPIC_MESSAGE_COUNT = 3
MIN_TOPIC_PARTICIPANT_COUNT = 2
SOURCE_EXCERPT_MAX_LENGTH = 160


def _build_summary_window(now: datetime) -> tuple[datetime, datetime]:
    window_end = now.astimezone(timezone.utc)
    window_start = datetime.combine(window_end.date(), time.min, tzinfo=timezone.utc)
    return window_start, window_end


def _render_message_line(message: MessageType) -> str:
    username = message.username or "unknown"
    created_at = message.created_at.isoformat() if message.created_at else "unknown"
    text = message.text.replace("\n", " ").strip()
    return f"[id={message.message_id}] [{created_at}] [{username}] {text}"


def _build_summary_prompt(messages: tuple[MessageType, ...]) -> str:
    rendered_messages = "\n".join(_render_message_line(message) for message in messages)
    instructions = "\n".join(
        (
            _("Summarize the chat day into one short general overview and several topic lines."),
            _("Each topic line must contain a short title, one fitting emoji, and the list of source message IDs."),
            _("Do not include any IDs that are not present in the provided transcript."),
            _("Skip one-off chatter that does not form a meaningful discussion."),
            _(
                "Prefer topics that include at least three messages or at least two participants, and avoid weak one-person fragments."
            ),
        )
    )
    return f"{instructions}\n\n{rendered_messages}"


def _normalize_excerpt(text: str) -> str:
    excerpt = " ".join(text.split())
    if len(excerpt) <= SOURCE_EXCERPT_MAX_LENGTH:
        return excerpt
    return f"{excerpt[: SOURCE_EXCERPT_MAX_LENGTH - 1].rstrip()}…"


def _build_source_excerpt(messages: list[MessageType]) -> str | None:
    for message in messages:
        if not message.text.strip():
            continue
        return _normalize_excerpt(message.text)
    return None


def _is_significant_topic(messages: list[MessageType]) -> bool:
    participant_count = len({message.user_id for message in messages})
    return len(messages) >= MIN_TOPIC_MESSAGE_COUNT or participant_count >= MIN_TOPIC_PARTICIPANT_COUNT


def _derive_summary_line(group: AIChatSummaryGroup, messages_by_id: dict[int, MessageType]) -> AIChatSummaryLine | None:
    grouped_messages = list(
        OrderedDict(
            (message_id, messages_by_id[message_id]) for message_id in group.message_ids if message_id in messages_by_id
        ).values()
    )
    if not grouped_messages:
        return None
    if not _is_significant_topic(grouped_messages):
        return None

    first_message = min(
        grouped_messages,
        key=lambda message: (
            message.created_at or datetime.min.replace(tzinfo=timezone.utc),
            message.message_id,
        ),
    )
    usernames = list(OrderedDict.fromkeys(message.username for message in grouped_messages if message.username))
    first_message_at = first_message.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return AIChatSummaryLine(
        emoji=group.emoji,
        title=group.title,
        first_message_id=first_message.message_id,
        first_message_at=first_message_at,
        usernames=usernames,
        source_excerpt=_build_source_excerpt(grouped_messages),
    )


def _build_message_url(chat_tid: int, message_id: int) -> str:
    chat_path = str(chat_tid).removeprefix("-100")
    return f"https://t.me/c/{chat_path}/{message_id}"


def _build_summary_line_doc(chat_tid: int, line: AIChatSummaryLine, current_locale: str) -> Template:
    message_url = _build_message_url(chat_tid, line.first_message_id)

    return Template(
        _("{time} - {emoji} {title}, {users}"),
        time=format_time(line.first_message_at.astimezone(timezone.utc), format="short", locale=current_locale),
        emoji=line.emoji,
        title=Url(Italic(line.title), message_url),
        users=HList(*line.usernames, divider=", ") if line.usernames else "-",
    )


def _build_summary_doc(chat_tid: int, summary_date: date, overview: str, lines: list[AIChatSummaryLine]) -> Doc:
    current_locale = get_i18n().current_locale
    sorted_lines = sorted(lines, key=lambda line: line.first_message_at)
    rendered_lines = VList(*[_build_summary_line_doc(chat_tid, line, current_locale) for line in sorted_lines])
    return Doc(
        Title(
            Template(
                _("Chat history of {today}"), today=format_date(summary_date, format="long", locale=current_locale)
            )
        ),
        overview,
        " ",
        BlockQuote(rendered_lines, expandable=True),
    )


def _track_summary_metrics(
    cached_message_count: int,
    grouped_message_count: int,
    covered_percentage: float,
    line_count: int,
    low_signal_line_count: int,
    generated: bool,
) -> None:
    if generated:
        count_metric("sophie.ai.chat_summaries.generated", attributes={"summary_kind": "daily"})
    count_metric(
        "sophie.ai.chat_summaries.lines_generated",
        line_count,
        attributes={"summary_kind": "daily"},
    )
    count_metric(
        "sophie.ai.chat_summaries.cached_messages",
        cached_message_count,
        attributes={"summary_kind": "daily"},
    )
    count_metric(
        "sophie.ai.chat_summaries.grouped_messages",
        grouped_message_count,
        attributes={"summary_kind": "daily"},
    )
    count_metric(
        "sophie.ai.chat_summaries.coverage_percent",
        covered_percentage,
        attributes={"summary_kind": "daily"},
    )
    count_metric(
        "sophie.ai.chat_summaries.low_signal_lines_skipped",
        low_signal_line_count,
        attributes={"summary_kind": "daily"},
    )


class GenerateChatSummaries:
    @staticmethod
    async def generate_summary_groups(messages: tuple[MessageType, ...], chat_iid: PydanticObjectId):
        history = NewAIMessageHistory()
        history.add_system(_("You summarize Telegram group discussions into structured topic lines."))
        history.add_custom(_build_summary_prompt(messages), name="Transcript")

        model = await get_chat_summary_model(chat_iid)
        service_tier = await get_service_tier("ai_chat_summaries_service_tier")
        result = await new_ai_generate_schema_with_result(
            history,
            AIChatSummaryGroups,
            model,
            user_tracking_id=chat_iid,
            service_tier=service_tier,
        )
        await charge_ai_usage(chat_iid, AI_FEATURE_CHATBOT, model, result.usage)
        return result.output

    @staticmethod
    async def send_summary(chat_tid: int, summary_date: date, overview: str, lines: list[AIChatSummaryLine]) -> None:
        await bot.send_message(chat_tid, _build_summary_doc(chat_tid, summary_date, overview, lines).to_html())

    async def process_chat(
        self,
        chat: ChatModel,
        summary_date: date,
        force: bool = False,
        target_chat_tid: int | None = None,
        now: datetime | None = None,
    ) -> None:
        existing_summary = None if force else await AIChatSummaryModel.get_for_date(chat.iid, summary_date)
        if existing_summary:
            log.debug(
                "generate_chat_summaries: summary already exists for date, skipping",
                chat=chat.tid,
                summary_date=summary_date,
                has_lines=bool(existing_summary.lines),
            )
            return

        current_time = now or datetime.now(timezone.utc)
        window_start, window_end = _build_summary_window(current_time)
        cached_messages = await get_cached_messages_between(chat.tid, window_start, window_end)
        if len(cached_messages) < 3:
            log.debug(
                "generate_chat_summaries: not enough messages, skipping",
                chat=chat.tid,
                count=len(cached_messages),
                window_start=window_start,
                window_end=window_end,
            )
            return

        groups = await self.generate_summary_groups(cached_messages, chat.iid)
        messages_by_id = {message.message_id: message for message in cached_messages}
        raw_group_message_ids = {
            message_id
            for message_id in chain.from_iterable(group.message_ids for group in groups.lines)
            if message_id in messages_by_id
        }
        lines = [
            line for line in (_derive_summary_line(group, messages_by_id) for group in groups.lines) if line is not None
        ]
        covered_message_ids = {line.first_message_id for line in lines}
        for group in groups.lines:
            grouped_messages = [
                messages_by_id[message_id] for message_id in group.message_ids if message_id in messages_by_id
            ]
            if not grouped_messages or not _is_significant_topic(grouped_messages):
                continue
            covered_message_ids.update(message.message_id for message in grouped_messages)

        grouped_message_count = len(raw_group_message_ids)
        covered_percentage = (
            round((len(covered_message_ids) / len(cached_messages)) * 100, 2) if cached_messages else 0.0
        )
        low_signal_line_count = max(len(groups.lines) - len(lines), 0)
        if not lines:
            log.debug("generate_chat_summaries: no summary lines generated", chat=chat.tid, summary_date=summary_date)
            await AIChatSummaryModel.upsert_for_date(chat, summary_date, groups.overview, [])
            _track_summary_metrics(
                len(cached_messages),
                grouped_message_count,
                covered_percentage,
                0,
                low_signal_line_count,
                generated=False,
            )
            return

        await AIChatSummaryModel.upsert_for_date(chat, summary_date, groups.overview, lines)
        await self.send_summary(target_chat_tid or chat.tid, summary_date, groups.overview, lines)
        _track_summary_metrics(
            len(cached_messages),
            grouped_message_count,
            covered_percentage,
            len(lines),
            low_signal_line_count,
            generated=True,
        )

    async def handle(self) -> None:
        if not await is_enabled("ai_chat_summaries"):
            log.debug("generate_chat_summaries: feature flag disabled, skipping run")
            return

        current_time = datetime.now(timezone.utc)
        summary_date = current_time.date()
        async for chat in ForChats():
            if not await AIEnabledModel.get_state(chat.iid):
                log.debug("generate_chat_summaries: AI disabled for chat, skipping", chat=chat.tid)
                continue

            async with UseChatLanguage(chat.iid):
                await self.process_chat(chat, summary_date, now=current_time)
