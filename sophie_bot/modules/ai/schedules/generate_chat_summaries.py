from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, date, datetime, time
from itertools import chain

from babel.dates import format_date, format_time
from beanie import PydanticObjectId
from stfu_tg import Doc, Heading, HList, Italic, ListItem, Template, UnorderedList, Url

from sophie_bot.db.models import AIChatSummaryLine, AIChatSummaryModel, ChatModel
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.modules.ai.json_schemas.chat_summary import AIChatSummaryGroup, AIChatSummaryGroups
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_summary_model_plan, resolve_chat_service_tier
from sophie_bot.modules.ai.utils.ai_header import (
    AIHeaderStyle,
    build_ai_header,
    build_ai_message_doc,
    get_ai_header_style,
)
from sophie_bot.modules.ai.utils.ai_mode import resolve_chat_capabilities
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message_to_chat
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages_between
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.summary_transcript import SummaryTranscript, build_summary_transcript
from sophie_bot.modules.utils_.scheduler.chat_language import UseChatLanguage
from sophie_bot.modules.utils_.scheduler.for_chats import ForChats
from sophie_bot.services.sentry_metrics import count_metric
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.i18n import get_i18n
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

MIN_TOPIC_MESSAGE_COUNT = 3
MIN_TOPIC_PARTICIPANT_COUNT = 2
SOURCE_EXCERPT_MAX_LENGTH = 160
SUMMARY_GENERATION_ATTEMPTS = 2


def _build_summary_window(now: datetime) -> tuple[datetime, datetime]:
    window_end = now.astimezone(UTC)
    window_start = datetime.combine(window_end.date(), time.min, tzinfo=UTC)
    return window_start, window_end


def _build_summary_prompt(transcript: SummaryTranscript, instructions: str) -> str:
    return f"{instructions}\n{transcript.format_instructions}\n\n{transcript.text}"


def _collect_unknown_refs(groups: AIChatSummaryGroups, messages_by_reference: dict[int, MessageType]) -> set[int]:
    return {
        reference
        for reference in chain.from_iterable(group.message_refs for group in groups.lines)
        if reference not in messages_by_reference
    }


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


def _resolve_group_messages(
    group: AIChatSummaryGroup, messages_by_reference: dict[int, MessageType]
) -> list[MessageType]:
    """Maps the model's transcript references back to messages, dropping hallucinated ones."""
    return list(
        OrderedDict(
            (reference, messages_by_reference[reference])
            for reference in group.message_refs
            if reference in messages_by_reference
        ).values()
    )


def _derive_summary_line(
    group: AIChatSummaryGroup, messages_by_reference: dict[int, MessageType]
) -> AIChatSummaryLine | None:
    grouped_messages = _resolve_group_messages(group, messages_by_reference)
    if not grouped_messages:
        return None
    if not _is_significant_topic(grouped_messages):
        return None

    first_message = min(
        grouped_messages,
        key=lambda message: (
            message.created_at or datetime.min.replace(tzinfo=UTC),
            message.message_id,
        ),
    )
    usernames = list(OrderedDict.fromkeys(message.username for message in grouped_messages if message.username))
    first_message_at = first_message.created_at or datetime.min.replace(tzinfo=UTC)
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
        time=format_time(line.first_message_at.astimezone(UTC), format="short", locale=current_locale),
        emoji=line.emoji,
        title=Url(Italic(line.title), message_url),
        users=HList(*line.usernames, divider=", ") if line.usernames else "-",
    )


def _build_summary_doc(
    chat_tid: int,
    summary_date: date,
    overview: str,
    lines: list[AIChatSummaryLine],
    header_style: AIHeaderStyle = "table",
) -> Doc:
    current_locale = get_i18n().current_locale
    sorted_lines = sorted(lines, key=lambda line: line.first_message_at)
    rendered_lines = (
        UnorderedList(*(ListItem(_build_summary_line_doc(chat_tid, line, current_locale)) for line in sorted_lines))
        if sorted_lines
        else None
    )
    title = Heading(
        Template(_("Chat history of {today}"), today=format_date(summary_date, format="long", locale=current_locale))
    )
    header = build_ai_header(header_style)

    return build_ai_message_doc(
        header_style,
        header,
        title,
        overview,
        rendered_lines,
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
    async def generate_summary_groups(
        transcript: SummaryTranscript, chat_iid: PydanticObjectId, chat_tid: int
    ) -> AIChatSummaryGroups:
        history = AIMessageHistory()
        instructions = str(await get_value("ai_chat_summaries_prompt", chat_tid=chat_tid))
        history.add_system(_("You summarize Telegram group discussions into structured topic lines."))
        history.add_custom(_build_summary_prompt(transcript, instructions), name="Transcript")

        model_plan = await get_chat_summary_model_plan(chat_iid, chat_tid=chat_tid)
        service_tier = await resolve_chat_service_tier(AIModelPurpose.summary, chat_iid, chat_tid)
        result = await run_structured_task(
            AIStructuredTask(output_type=AIChatSummaryGroups, feature=AI_FEATURE_CHATBOT),
            model_plan,
            history,
            chat_iid=chat_iid,
            chat_tid=chat_tid,
            service_tier=service_tier,
        )
        return result.output

    async def generate_verified_summary_groups(
        self, transcript: SummaryTranscript, chat: ChatModel, *, strict: bool
    ) -> AIChatSummaryGroups | None:
        """Generates summary groups, retrying once when the model invents transcript references.

        Under the anonymized transcript a hallucinated reference silently points at the wrong real
        message, so the whole run is discarded rather than published when a retry does not fix it.
        """
        if not strict:
            return await self.generate_summary_groups(transcript, chat.iid, chat.tid)

        for attempt in range(1, SUMMARY_GENERATION_ATTEMPTS + 1):
            groups = await self.generate_summary_groups(transcript, chat.iid, chat.tid)
            unknown_refs = _collect_unknown_refs(groups, transcript.messages_by_reference)
            if not unknown_refs:
                return groups
            log.warning(
                "generate_chat_summaries: model referenced unknown transcript lines",
                chat=chat.tid,
                attempt=attempt,
                unknown_ref_count=len(unknown_refs),
            )
            count_metric(
                "sophie.ai.chat_summaries.unknown_refs",
                len(unknown_refs),
                attributes={"summary_kind": "daily"},
            )

        log.warning("generate_chat_summaries: discarding summary after failed retry", chat=chat.tid)
        return None

    @staticmethod
    async def send_summary(chat_tid: int, summary_date: date, overview: str, lines: list[AIChatSummaryLine]) -> None:
        header_style = await get_ai_header_style("summary", chat_tid)
        await send_ai_rich_message_to_chat(
            chat_tid,
            _build_summary_doc(chat_tid, summary_date, overview, lines, header_style),
        )

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

        current_time = now or datetime.now(UTC)
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

        anonymize = await is_enabled("ai_summary_improved_privacy", chat_tid=chat.tid)
        transcript = build_summary_transcript(cached_messages, anonymize=anonymize)
        groups = await self.generate_verified_summary_groups(transcript, chat, strict=anonymize)
        if groups is None:
            return

        messages_by_reference = transcript.messages_by_reference
        known_refs = {
            reference
            for reference in chain.from_iterable(group.message_refs for group in groups.lines)
            if reference in messages_by_reference
        }
        lines = [
            line
            for line in (_derive_summary_line(group, messages_by_reference) for group in groups.lines)
            if line is not None
        ]
        covered_message_ids = {line.first_message_id for line in lines}
        for group in groups.lines:
            grouped_messages = _resolve_group_messages(group, messages_by_reference)
            if not grouped_messages or not _is_significant_topic(grouped_messages):
                continue
            covered_message_ids.update(message.message_id for message in grouped_messages)

        grouped_message_count = len(known_refs)
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
        current_time = datetime.now(UTC)
        summary_date = current_time.date()
        async for chat in ForChats():
            if not await is_enabled("ai_chat_summaries", chat_tid=chat.tid):
                log.debug("generate_chat_summaries: feature flag disabled, skipping chat", chat=chat.tid)
                continue
            if not (await resolve_chat_capabilities(chat)).message_cache:
                log.debug("generate_chat_summaries: AI disabled for chat, skipping", chat=chat.tid)
                continue

            async with UseChatLanguage(chat.iid):
                await self.process_chat(chat, summary_date, now=current_time)
